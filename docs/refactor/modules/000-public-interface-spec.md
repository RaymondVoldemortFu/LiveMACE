# 公共扩展接口规范 v1

本文冻结模块任务共同实现的公开接口。接口位置以最终包 `benchmark` 表示；落地时 Python 源码目录为 `backend/benchmark/`。公开接口版本为 `1`，内部模块不得要求扩展导入 `backend/services/*`、SQLAlchemy model 或 FastAPI 对象。

## 1. 稳定性等级

| 等级 | 含义 | 兼容规则 |
| --- | --- | --- |
| Public SPI | 第三方实现的接口 | v1 内不得破坏签名；新增字段必须有默认值 |
| Public DTO | 配置/API/trace 共享数据 | 只允许向后兼容增加字段 |
| Internal | 系统内部实现 | 可重构，不向扩展承诺 |

公开命名空间仅包括：

```python
benchmark.contracts
benchmark.agents
benchmark.tools
benchmark.prompts
benchmark.providers
benchmark.extensions
benchmark.testing
```

## 2. 通用类型

```python
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

class Market(str, Enum):
    CRYPTO = "CRYPTO"
    US = "US"

@dataclass(frozen=True)
class ExtensionRef:
    id: str                 # reverse-DNS 或 project.slug，ASCII 小写
    version: str            # SemVer
    api_version: int = 1

@dataclass(frozen=True)
class AccountView:
    id: int
    name: str
    initial_capital: Decimal
    current_cash: Decimal
    frozen_cash: Decimal
    margin_used: Decimal

@dataclass(frozen=True)
class PositionView:
    symbol: str
    market: Market
    quantity: Decimal
    available_quantity: Decimal
    avg_cost: Decimal
    leverage: int
    side: str | None

@dataclass(frozen=True)
class PortfolioView:
    account: AccountView
    positions: tuple[PositionView, ...]
    prices: Mapping[str, Decimal]
    total_assets: Decimal
    captured_at: datetime

@dataclass(frozen=True)
class DecisionContext:
    account_id: int
    decision_round_id: str
    trace_id: str
    portfolio: PortfolioView
    config: Mapping[str, JsonValue]
    started_at: datetime
```

DTO 必须不可变；扩展拿到的是只读快照，不能拿 ORM entity 或 session。实现补充：各 DTO 在 `__post_init__` 做构造期类型/取值校验，mapping 字段冻结为只读视图；`benchmark.contracts` 另导出确定性序列化辅助 `to_jsonable(value) -> JsonValue`（Decimal 转字符串、datetime 转 UTC ISO8601、拒绝 NaN/Infinity），属 Public API。

## 3. Agent SPI

```python
class TerminationReason(str, Enum):
    TRADE_DONE = "trade_done"
    HOLD = "hold"
    MAX_STEPS = "max_steps"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    CANCELLED = "cancelled"

@dataclass(frozen=True)
class ExecutedTradeRef:
    operation: str
    symbol: str
    market: Market
    order_id: int | None
    trade_id: int | None
    executed: bool
    reject_code: str | None = None

@dataclass(frozen=True)
class AgentRunResult:
    trace_id: str
    decision_round_id: str
    termination_reason: TerminationReason
    executed_trades: tuple[ExecutedTradeRef, ...] = ()
    summary: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

class Agent(Protocol):
    def run(self, context: DecisionContext) -> AgentRunResult: ...

class AgentFactory(Protocol):
    def create(self, context: "AgentBuildContext", config: Mapping[str, JsonValue]) -> Agent: ...

@dataclass(frozen=True)
class AgentBuildContext:
    llm: "LLMClientPort"
    tools: "ToolInvoker"
    prompts: "PromptResolver"
    events: "EventSink"
```

约束：

- `Agent.run()` 必须是同步接口，并在调用结束前返回完整 `AgentRunResult`。
- 系统使用 `ThreadPoolExecutor` 按账户并行运行 Agent；一个账户任务对应一个 worker，不在系统层创建 asyncio task。
- 单个 Agent 内的 LLM、工具和交易调用对系统表现为同步、顺序执行。外部 Agent 如需 asyncio、子线程或自己的线程池，必须完全封装在自己的同步 `run()` 内。
- 系统不接受 `Agent.run()` 返回 coroutine/awaitable，不提供 event-loop、异步 Tool 或异步 Provider 兼容层；返回 awaitable 视为 `AgentRuntimeError`。
- v1 没有“建议交易 JSON”。交易只能通过 `ToolInvoker.call("core.execute_trade", ...)` 执行，或明确 HOLD。
- `AgentRunResult.executed_trades` 是执行引用，不是待执行命令；上层不得再次执行。
- Agent 不得 import 或调用 `order_matching`、`order_executor_leverage`、repository。

实现补充：`Agent`/`AgentFactory` Protocol 标注 `@runtime_checkable`；`benchmark.agents` 另导出注册与运行时公开类型 `AgentDescriptor`、`AgentSelection`、`RegisteredAgent`、`AgentRuntimeEvent`、`NullEventSink`、`AgentRegistry`、`AgentRuntime`（事件类型见第 6 节 EventSink 说明）。

## 4. Tool SPI

```python
class SideEffect(str, Enum):
    READ_ONLY = "read_only"
    EXTERNAL_READ = "external_read"
    MEMORY_WRITE = "memory_write"
    SANDBOX_WRITE = "sandbox_write"
    TRADING_WRITE = "trading_write"

@dataclass(frozen=True)
class ToolSpec:
    name: str                       # namespace.name；core.* 保留给内置工具
    description: str
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    side_effect: SideEffect
    timeout_seconds: float = 30.0
    cacheable: bool = False
    required_capabilities: tuple[str, ...] = ()

@dataclass(frozen=True)
class ToolContext:
    account_id: int
    decision_round_id: str
    trace_id: str
    call_id: str
    capabilities: frozenset[str]
    deadline_at: datetime | None = None  # cooperative deadline; Invoker always supplies it

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    value: JsonValue = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...
    def invoke(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> ToolResult: ...

class ToolProvider(Protocol):
    def list_tools(self) -> Sequence[Tool]: ...

class ToolInvoker(Protocol):
    def call(self, name: str, arguments: Mapping[str, JsonValue]) -> ToolResult: ...

class ToolSpecSource(Protocol):
    def list_specs(self) -> Sequence[ToolSpec]: ...
```

执行顺序固定为：名称解析与 capability/enabled 检查（未启用工具返回 `TOOL_NOT_ACTIVE`）-> 参数 JSON 可序列化检查 -> JSON Schema 输入校验 -> deadline 预检 -> cache 查找（`cacheable=True` 且命中时直接返回缓存结果）-> 同步 tool invoke -> invoke 后按单调时钟复检 timeout/deadline -> 输出 JSON 可序列化与 schema 校验（仅 `ok=True` 结果）-> cache 写入 -> trace/event。

补充语义：

- 同步模型下无法在 invoke 中途强制中断，timeout 语义为“invoke 前 deadline 预检 + invoke 后墙钟复检”；带写副作用的超时结果仅在 metadata 标记，不替换结果。
- 事件贯穿全管线发出（denied/failed/started/cache_hit/completed），`tool.started` 在 invoke 之前发出，并非只在末尾单步。
- redaction 只作用于写入 trace/event 的 arguments 与 result 副本；返回给 Agent 的 `ToolResult` 保持原样，不脱敏。

`ToolInvoker.call()` 在工具完成前阻塞，Agent 得到 `ToolResult` 后才进入下一步。Tool 的业务拒绝返回 `ToolResult(ok=False)`；只有框架故障抛 `ToolRuntimeError`。异步 `invoke()` 或 awaitable 返回值不属于 v1 接口。

保留 namespace `core.*`：`core.execute_trade`、`core.market_snapshot`、`core.kline_history`、`core.account_state`、`core.decision_history`、`core.memory_add`、`core.memory_search`、`core.sandbox_*`、`core.search`。第三方必须使用自己的 namespace。

实现补充：`Tool`/`ToolProvider`/`ToolInvoker` Protocol 标注 `@runtime_checkable`；`ToolSpecSource` 是 Agent 可选消费的只读 schema 边界，不改变只实现 `ToolInvoker.call()` 的最小契约；`benchmark.tools` 另导出 `ToolCache`、`ToolEventSink`、`RegisteredTool`、`ToolRuntimeEvent` 等运行时公开类型。

## 5. Prompt SPI

```python
@dataclass(frozen=True)
class PromptSpec:
    id: str                         # namespace.prompt-name
    version: str
    required_variables: tuple[str, ...]
    optional_variables: Mapping[str, JsonValue] = field(default_factory=dict)
    content_type: str = "text/plain"

@dataclass(frozen=True)
class RenderedPrompt:
    spec: PromptSpec
    content: str
    content_sha256: str

class PromptProvider(Protocol):
    def list_prompts(self) -> Sequence[PromptSpec]: ...
    def render(self, prompt_id: str, variables: Mapping[str, JsonValue]) -> RenderedPrompt: ...

class PromptResolver(Protocol):
    def render(self, prompt_id: str, variables: Mapping[str, JsonValue]) -> RenderedPrompt: ...
```

Prompt 文件使用 UTF-8，模板引擎 v1 只支持命名变量，不执行任意 Python。缺失变量、未知变量、模板语法错误必须在扩展装载或账户配置校验时暴露，不得等到交易轮次中才失败。

Prompt override 以完整 `prompt_id` 替换；不支持按字符串位置 patch。相同 id 的优先级依次为：账户显式 profile > 外部启用扩展 > 内置扩展（对应 `PromptSourcePriority` 的 `ACCOUNT > EXTERNAL > BUILTIN`）。

冲突判定粒度为 `(prompt_id, version, priority)`：同一三元组重复注册导致 catalog invalid（`PROMPT_VERSION_PRIORITY_CONFLICT`），不静默覆盖。同一 `prompt_id` 在同一优先级下允许多个 version 共存（来自**不同** PromptProvider / 不同扩展）；解析时先取最高优先级，再取该优先级内最高 SemVer 版本。

单个 `PromptProvider` 以及单个 Prompt directory 对同一 `prompt_id` 仅允许暴露一个 version（`PROMPT_PROVIDER_ID_CONFLICT`）；同 ID 的多版本必须由不同 provider 分别注册。`PromptProvider.render` 仅接收 `prompt_id`：版本选择由 registry 在调用前完成，并路由到持有该 version 的 provider 实例。

配套公开 DTO：`PromptSelection`、`PromptProfileDescriptor`（`benchmark.contracts`）。`PromptRegistry.render` 支持 keyword-only 可选参数 `version: str | None = None` 用于钉住特定版本。

## 6. Provider Ports

除 `LLMClientPort` 外，Provider port 均携带自描述元数据属性：`id: str`、`version: str`、`capabilities: tuple[str, ...]`、`config_schema: Mapping[str, JsonValue]`。

```python
class LLMClientPort(Protocol):
    def complete(self, request: "LLMRequest") -> "LLMResponse": ...

class MemoryStorePort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def search(
        self,
        account_id: int | str,
        query: str,
        limit: int,
        *,
        market: Market,
    ) -> Sequence["MemoryRecord"]: ...

    def add(
        self,
        account_id: int | str,
        content: str,
        metadata: Mapping[str, JsonValue],
        *,
        market: Market,
    ) -> str: ...

    def delete_all(self, account_id: int | str) -> int: ...
    def healthcheck(self) -> "HealthStatus": ...

class MarketDataPort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def get_price(self, symbol: str, market: Market) -> "PriceResult": ...
    def get_klines(self, query: "KlineQuery") -> "KlineResult": ...
    def get_market_status(self, symbol: str, market: Market) -> "MarketStatusResult": ...
    def healthcheck(self) -> "HealthStatus": ...

class SandboxPort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def lease(self, account_id: int) -> "SandboxLease": ...
    def release(self, lease: "SandboxLease") -> None: ...
    def healthcheck(self) -> "HealthStatus": ...
```

事件下沉接口按运行时分为两套，事件负载类型不同，不设统一 `RuntimeEvent`：

```python
# benchmark.agents
class EventSink(Protocol):
    def emit(self, event: "AgentRuntimeEvent") -> None: ...

# benchmark.tools
class ToolEventSink(Protocol):
    def emit(self, event: "ToolRuntimeEvent") -> None: ...
```

`AgentRuntimeEvent.type` 允许：`agent.started`、`agent.completed`、`agent.failed`、`agent.cancelled`、`agent.step`。`agent.step` 对应内置 Agent 的逐步回调；`metadata` 必须包含 1-based `step_number` 和 `role`，并保留 `content`、`tool_calls`、`name`、`tool_call_id` 等消息字段。持久化到 `AgentTrace` 由 M16 完成。

Provider port 与 Agent/Tool 一样采用同步接口。Provider 错误统一包含 `code`、`message`、`retryable`、`provider_id`；四字段由 `benchmark.contracts.ProviderError` 直接承载（`retryable`/`provider_id` 为 keyword-only、有默认值），provider adapter 内完成第三方异常归一化。第三方扩展可在自身实现内部使用异步 I/O，但必须同步返回 port 规定的结果，系统不负责驱动其 event loop。

## 7. Trade Command Gateway

```python
@dataclass(frozen=True)
class TradeCommand:
    account_id: int
    operation: str                  # open|close|hold|all_in|close_all
    market: Market
    symbol: str
    direction: str | None
    sizing_mode: str | None
    sizing_value: Decimal | None
    leverage: int
    reason: str
    idempotency_key: str

@dataclass(frozen=True)
class TradeCommandResult:
    accepted: bool
    executed: bool
    reject_code: str | None
    reject_message: str | None
    order_id: int | None
    trade_id: int | None
    normalized_command: TradeCommand
    raw_result: Mapping[str, JsonValue] = field(default_factory=dict)  # 底层执行器原始结果快照；构造期 to_jsonable + 递归冻结

class TradeCommandGateway(Protocol):
    def execute(self, command: TradeCommand) -> TradeCommandResult: ...
```

`TradeCommand` / `TradeCommandResult` DTO 位于 `benchmark.contracts`。`TradeCommandGateway` Protocol 及其实现位于 `benchmark.application.trading`（系统侧接口，不属于第 1 节的扩展公开命名空间）：扩展只能通过 `core.execute_trade` 工具间接触发交易，不得直接 import gateway。实现类上除 `execute` 之外的方法（如 `create_order`、`cancel_order`、`process_pending`）为 Internal，不对扩展承诺。

HTTP、WS 和 `core.execute_trade` 都必须调用同一个 gateway。`idempotency_key` 在 Agent 工具中固定为 `{decision_round_id}:{tool_call_id}`。Gateway 是唯一允许协调订单、成交、持仓和现金写入的应用接口。

## 7.1 系统并发模型

系统并发边界固定如下：

```text
DecisionRoundService.run()                         # 同步入口
  -> ThreadPoolExecutor(max_workers=AGENT_MAX_CONCURRENCY)
     -> account worker A -> AgentRuntime.run()     # 同步
     -> account worker B -> AgentRuntime.run()     # 同步
     -> account worker C -> AgentRuntime.run()     # 同步

单个 account worker 内：
  LLM.complete()
    -> ToolInvoker.call()
       -> Tool.invoke()
       -> [交易工具] TradeCommandGateway.execute()
    -> 下一次 LLM.complete()
```

- 不同账户 Agent 通过系统线程池并行。
- 每个 worker 使用独立的数据库 session/UoW，并在 `finally` 中关闭。
- 单个 Agent 的步骤和内置工具调用默认顺序、同步执行。
- 系统不复用一个 session 到多个线程，也不在不同账户间共享可变 Agent 实例。
- 外部 Agent 内部并发完全由扩展自行实现和测试，不属于系统兼容承诺。

## 8. 扩展 Manifest

文件名固定为 `alpha-arena-extension.yaml`：

```yaml
api_version: 1
id: com.example.my-extension
version: 1.2.0
name: My Extension
description: Example extension
python:
  requires: ">=3.10"
  entrypoint: "my_extension:extension"
components:
  agents:
    - id: com.example.my-agent
      factory: "my_extension.agents:create_factory"
      config_schema: "schemas/agent.schema.json"
  tools:
    - provider: "my_extension.tools:create_provider"
  prompts:
    - directory: "prompts"
      index: "prompts/index.yaml"
capabilities:
  requested:
    - market.read
    - sandbox.write
```

Manifest 路径必须相对扩展根目录：禁止绝对路径与 `..` 分段，且路径在 `resolve()` 后（含 symlink 解析）不得逃逸扩展根目录。结构约束：manifest 必须至少声明一类 component；声明了 `agents` 或 `tools` 时必须提供 `python` 段。装载阶段只 import 声明的 entrypoint；不扫描并执行任意 Python 文件。

## 9. 账户扩展配置

公开 DTO：

```json
{
  "agent_id": "core.react",
  "agent_config": {},
  "toolset_ids": ["core.default-tools"],
  "disabled_tools": [],
  "prompt_profile_id": "core.react.default",
  "component_versions": {
    "core.react": "1.0.0"
  }
}
```

账户保存时必须验证：组件存在、版本可用、config 符合 schema、capability 已授权、Prompt 变量完整。运行时只读取已验证且已解析的配置；扩展被卸载时，引用它的账户变为 `configuration_invalid`，不得自动回退到另一 Agent。

## 10. 错误规范

公共异常只用于框架级错误。所有公共异常继承自 `benchmark.contracts.BenchmarkError`（携带 `message`/`code`/`details`，提供 `to_dict()`）：

```text
BenchmarkError                # 基类
ExtensionManifestError
ExtensionLoadError
ComponentNotFoundError
ComponentConflictError
ComponentConfigError
AgentRuntimeError
ToolRuntimeError
PromptRenderError
ProviderError                 # 额外携带 retryable / provider_id
TradeGatewayError
```

各公开子包另导出注册期异常：`PromptLoadError`、`PromptRegistryFrozenError`（`benchmark.prompts`）、`AgentRegistryFrozenError`（`benchmark.agents`）、`ToolRegistryFrozenError`（`benchmark.tools`）。它们均派生自上表基类，扩展可按基类捕获。

API 错误格式保持：

```json
{
  "error": {
    "code": "COMPONENT_CONFIG_INVALID",
    "message": "...",
    "details": {},
    "request_id": "..."
  }
}
```

错误 message 面向用户，details 不得含 API key、完整 LLM 请求或未脱敏文件内容。

## 11. 版本与兼容

- manifest `api_version` 不支持时拒绝整个扩展，不部分装载。
- component `id` 在全局唯一；版本使用 SemVer。
- v1 内接口新增参数必须为 keyword-only 且有默认值。
- 系统启动日志列出装载的 extension/component id、version 和来源，不记录密钥。
- 账户保存 component version，用于 trace 可复现；系统不得在运行中静默切换版本。

## 附录 A：条款落地波次对照（非规范性）

本规范描述 v1 目标态。以下条款在当前开发进度（Wave 1 接口骨架）下尚未落地，属计划内空窗，接口评审时不视为违例；落地波次以 `module-groups.md` 为准：

| 条款 | 归属模块 | 计划波次 |
| --- | --- | --- |
| §3/§7.1 `DecisionRoundService` 经 ThreadPoolExecutor 编排 `AgentRuntime.run()`（当前委托 legacy 调度） | M10 | Wave 3 |
| §4 `core.*` 内置工具在 `benchmark/builtin/tools` 落地（当前为 legacy 无前缀实现 + 名称别名映射） | M06 | Wave 2 |
| §5 账户显式 profile 优先级（`PromptSourcePriority.ACCOUNT`）接线 | M12 | Wave 2/3 |
| §7 HTTP/WS 下单统一经 `TradeCommandGateway`（当前直调 `order_matching`） | M21 | Wave 3 |
| §7 Agent 工具强制 `{decision_round_id}:{tool_call_id}` idempotency key（当前 legacy 工具层允许显式覆盖） | M06 | Wave 2 |
| §7.1 决策 worker 使用独立 UoW（当前为独立 `SessionLocal` + finally 关闭） | M10 | Wave 3 |
| §8 装载阶段只 import 声明 entrypoint（loader/discovery/catalog 未实现） | M13 | Wave 2/3 |
| §9 账户扩展配置整节（DTO、保存校验、`configuration_invalid`） | M12 | Wave 2/3 |
| §10 API 错误信封（`error` 包装 + `request_id`） | M14/M21 | Wave 3 |
| §11 跨扩展 component id 全局唯一仲裁、启动装载日志 | M13 | Wave 2/3 |
| §11 账户钉 `component_versions`、禁止运行中静默切换版本 | M12 | Wave 2/3 |
