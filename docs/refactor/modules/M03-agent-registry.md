# M03：Agent SPI、Factory 与 Registry

## 交付目标

用稳定 Agent SPI 替换 `BaseAgent` 和 `factory.py` 中硬编码的 `if/elif agent_type`。本任务实现框架，不迁移具体 Agent。

## 文件边界

- 新增：`backend/alpha_arena/agents/{__init__,protocol,registry,runtime,errors}.py`。
- 修改：`backend/services/agent/base.py`、`factory.py` 仅增加过渡 adapter/deprecation import。
- 禁止修改：`react.py`、`multi_agent*.py`、`rule_aware/*`、Prompt 和工具实现。

## 暴露接口

```python
class AgentRegistry:
    def register(self, descriptor: AgentDescriptor, factory: AgentFactory) -> None
    def unregister(self, agent_id: str) -> None
    def get(self, agent_id: str, version: str | None = None) -> RegisteredAgent
    def list(self) -> tuple[AgentDescriptor, ...]
    def validate_config(self, agent_id: str, config: Mapping[str, JsonValue]) -> ValidationReport

class AgentRuntime:
    def run(self, selection: AgentSelection, context: DecisionContext) -> AgentRunResult
```

Registry 写入只允许 bootstrap 阶段；startup 完成后 `freeze()`，运行时只读。重复 `(id, version)` 报 `ComponentConflictError`。

## TODO

- [ ] 实现 Agent Protocol、Factory、Descriptor、Selection 和 Registry。
- [ ] config 使用 manifest 引用的 JSON Schema 校验；默认值只由 schema 注入一次。
- [ ] Runtime 创建 Agent，记录开始/结束事件，验证返回 `trace_id`、round id 与 context 一致。
- [ ] Runtime 在当前账户 worker 中直接同步调用 Agent；Runtime 自身不创建线程、asyncio task 或 event loop。
- [ ] Runtime 统一 deadline/cancellation 状态检查；不得捕获并伪装进程级异常。
- [ ] 检查 `Agent.run()` 返回值不是 coroutine/awaitable；违规时抛 `AgentRuntimeError`，不自动兼容。
- [ ] 现有同步 Agent 直接使用 adapter 转换 context/result 和 trace callback，不再额外放入第二层 worker thread。
- [ ] 将 `factory.create_agent()` 暂时改为 registry facade，并标记 internal deprecated；M04 后删除硬编码分支。
- [ ] 增加并发读取和 freeze 后禁止注册测试。

## 验收

- 一个 tests 内定义的第三方 Agent 可仅依赖 `alpha_arena.agents` 注册并运行。
- 未知 Agent、版本冲突、坏 config、错误返回 context id 均得到规定错误。
- 同步 Agent 在调用线程内执行；返回 awaitable 的扩展 Agent 被拒绝。
- Registry 和 runtime 不 import 任一内置 Agent 实现。

## 前置与并行

前置 M01。可与 M05/M07/M09/M11 并行；M04、M13 依赖本任务。
