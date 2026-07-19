# M03 Agent SPI、Registry 与 Runtime 实现报告

## 这次改动解决什么问题

改动前，系统通过 `services/agent/factory.py` 中的 `if/elif` 直接选择具体 Agent。新增 Agent 时，开发者必须修改系统工厂代码，并且需要了解旧 `BaseAgent`、LLM client 和工具注册表的内部结构。

M03 把“系统如何找到并运行 Agent”抽成一层公开接口。第三方 Agent 只需实现同步 `run(context)`，提供 factory 和配置 schema，即可注册到系统，不需要 import `services`、数据库 model 或 FastAPI 对象。

这次只改变结构和接口，不改变 ReAct、MultiAgent、AdvancedMultiAgent、RuleAware 的决策算法，也没有改变账户级线程池调度方式。

## 核心接口

公开入口位于 `benchmark.agents`：

```python
class Agent(Protocol):
    def run(self, context: DecisionContext) -> AgentRunResult: ...

class AgentFactory(Protocol):
    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, JsonValue],
    ) -> Agent: ...

class AgentRegistry:
    def register(self, descriptor, factory) -> None: ...
    def get(self, agent_id, version=None) -> RegisteredAgent: ...
    def validate_config(self, agent_id, config, version=None) -> ValidationReport: ...
    def freeze(self) -> None: ...

class AgentRuntime:
    def run(self, selection, context, *, deadline_at=None, is_cancelled=None): ...
```

`AgentDescriptor` 说明 Agent 的稳定 id、SemVer 版本和 JSON Schema。`AgentSelection` 表示某次运行选择哪个 Agent、哪个版本以及传入什么配置。`AgentBuildContext` 只暴露 LLM、Tool、Prompt 和 Event 四个端口，不暴露 ORM session。

## Registry 的处理思路

Registry 是 Agent 的目录，不负责执行 Agent。

- key 固定为 `(agent_id, version)`，重复注册直接报 `ComponentConflictError`。
- 不指定版本时选择最高 SemVer；指定版本时精确匹配。
- 注册阶段检查 config schema 本身是否合法。
- 运行配置使用 JSON Schema 校验。schema 中的默认值会写入配置副本，不修改调用方传入的 dict；再次校验不会重复追加默认值。
- bootstrap 完成后调用 `freeze()`。冻结后禁止注册和删除，但允许多个账户 worker 并发读取。
- 未知 Agent 统一报 `ComponentNotFoundError`，坏配置统一报 `ComponentConfigError`。

扩展 manifest 中的 `config_schema` 文件仍由后续 loader/bootstrap 读取。Registry 接收的是已经解析好的 schema mapping，因此 Agent 层不需要知道扩展目录或文件路径。

## Runtime 的同步执行边界

Runtime 不创建线程、asyncio task 或 event loop。调用链保持为：

```text
账户 ThreadPoolExecutor worker
  -> AgentRuntime.run()
     -> AgentFactory.create()
     -> Agent.run()
     -> AgentRunResult
```

所以现有“不同账户并行、单个 Agent 内同步顺序执行”的规则没有变化。测试会记录线程 id，确认 `Agent.run()` 就在调用 Runtime 的 worker 线程内运行。

Runtime 在调用前检查 cancellation 和 deadline。由于系统不使用异步中断，它不会在 Agent 运行一半时从另一个线程强行终止 Agent；外部 Agent 如需内部并发或更细的取消机制，需要在自己的同步 `run()` 内封装。

如果 factory 或 `Agent.run()` 返回 coroutine/awaitable，Runtime 会关闭该 coroutine 并抛出 `AgentRuntimeError`，不会尝试自动 `await`。这保证系统不会意外引入第二套调度模型。

Runtime 还会检查：

- 返回值必须是 `AgentRunResult`；
- `trace_id` 必须和输入 `DecisionContext` 一致；
- `decision_round_id` 必须和输入一致；
- 普通扩展异常会归一化成 `AgentRuntimeError`，原始异常保留为 `__cause__`，错误文本不会直接暴露 provider 内容；
- `KeyboardInterrupt`、`SystemExit`、`GeneratorExit` 不会被包装成普通 Agent 错误。

运行开始、完成、失败和取消会通过 `EventSink` 发出不可变的 `AgentRuntimeEvent`。

## 旧设计如何兼容

现有调用方仍可继续使用：

```python
create_agent(agent_type, llm, tools, **kwargs)
```

这个函数的签名和返回值没有改变，但内部选择逻辑已改成 registry facade。旧名称会映射为稳定 id：

| 旧 `agent_type` | Registry id |
| --- | --- |
| `react`、`default` | `core.react` |
| `multi_agent` | `core.multi-agent` |
| `advanced_multi_agent` | `core.advanced-multi-agent` |
| `rule_aware` | `core.rule-aware` |

旧 Agent 仍返回原来的 dict，保证当前交易调用链不变。`LegacyAgentAdapter` 已提供 `DecisionContext -> 旧 portfolio/prices -> AgentRunResult` 的同步转换，供 M04 逐个迁移内置 Agent；M03 不提前改写具体 Agent 文件。

## 第三方 Agent 的最小接入方式

```python
class MyAgent:
    def run(self, context):
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary="no trade",
        )

class MyFactory:
    def create(self, context, config):
        return MyAgent()

registry.register(
    AgentDescriptor(
        id="com.example.my-agent",
        version="1.0.0",
        config_schema={"type": "object", "additionalProperties": False},
    ),
    MyFactory(),
)
```

扩展代码只依赖 `benchmark.agents` 和 `benchmark.contracts`。

## 文件改动

- `backend/benchmark/agents/protocol.py`：SPI、descriptor、selection、validation 和事件 DTO。
- `backend/benchmark/agents/registry.py`：注册、版本解析、schema 默认值和 freeze。
- `backend/benchmark/agents/runtime.py`：同步运行、取消/deadline、结果和异常校验。
- `backend/benchmark/agents/errors.py`：Agent 层错误出口。
- `backend/services/agent/factory.py`：旧工厂改为 registry facade。
- `backend/services/agent/base.py`：增加 `LegacyAgentAdapter`。
- `backend/tests/agents/`：第三方 Agent、并发读取、配置、同步和错误边界测试。

## 验证结果

- M03 + M00/M01 及旧包名移除测试：49 个通过。
- 既有 Agent、交易语义、baseline 和线程隔离回归：21 个通过。
- Ruff 静态检查通过。
- 公共 Agent framework 有 import-boundary 测试，不允许 import `services`、`database`、`api`、FastAPI 或 SQLAlchemy。

## 后续模块边界

M03 只建立 Agent 框架。以下工作留给后续模块：

- M04：把四类内置 Agent 逐个迁移为公开 SPI factory，并最终移除旧 dict adapter。
- M09：用正式 Provider Port 类型替换 `AgentBuildContext` 中当前的结构化端口占位。
- M13：由 Extension Catalog 统一装载扩展 descriptor，并在 bootstrap 结束时冻结 registry。
