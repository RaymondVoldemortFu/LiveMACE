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

DTO 必须不可变；扩展拿到的是只读快照，不能拿 ORM entity 或 session。

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
```

执行顺序固定为：名称解析 -> capability 检查 -> JSON Schema 输入校验 -> deadline/timeout 检查 -> 同步 tool invoke -> 输出 schema 校验 -> redaction -> trace/event。`ToolInvoker.call()` 在工具完成前阻塞，Agent 得到 `ToolResult` 后才进入下一步。Tool 的业务拒绝返回 `ToolResult(ok=False)`；只有框架故障抛 `ToolRuntimeError`。异步 `invoke()` 或 awaitable 返回值不属于 v1 接口。

保留 namespace `core.*`：`core.execute_trade`、`core.market_snapshot`、`core.kline_history`、`core.account_state`、`core.decision_history`、`core.memory_add`、`core.memory_search`、`core.sandbox_*`、`core.search`。第三方必须使用自己的 namespace。

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

Prompt override 以完整 `prompt_id` 替换；不支持按字符串位置 patch。相同 id 的优先级依次为：账户显式 profile > 外部启用扩展 > 内置扩展。相同优先级冲突导致 catalog invalid，不静默覆盖。

## 6. Provider Ports

```python
class LLMClientPort(Protocol):
    def complete(self, request: "LLMRequest") -> "LLMResponse": ...

class MemoryStorePort(Protocol):
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
    def get_price(self, symbol: str, market: Market) -> "PriceResult": ...
    def get_klines(self, query: "KlineQuery") -> "KlineResult": ...
    def get_market_status(self, symbol: str, market: Market) -> "MarketStatusResult": ...

class SandboxPort(Protocol):
    def lease(self, account_id: int) -> "SandboxLease": ...

class EventSink(Protocol):
    def emit(self, event: "RuntimeEvent") -> None: ...
```

Provider port 与 Agent/Tool 一样采用同步接口。Provider 错误统一包含 `code`、`message`、`retryable`、`provider_id`；provider adapter 内完成第三方异常归一化。第三方扩展可在自身实现内部使用异步 I/O，但必须同步返回 port 规定的结果，系统不负责驱动其 event loop。

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

class TradeCommandGateway(Protocol):
    def execute(self, command: TradeCommand) -> TradeCommandResult: ...
```

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

Manifest 路径必须相对扩展根目录，禁止 `..` 逃逸。装载阶段只 import 声明的 entrypoint；不扫描并执行任意 Python 文件。

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

公共异常只用于框架级错误：

```text
ExtensionManifestError
ExtensionLoadError
ComponentNotFoundError
ComponentConflictError
ComponentConfigError
AgentRuntimeError
ToolRuntimeError
PromptRenderError
ProviderError
TradeGatewayError
```

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
