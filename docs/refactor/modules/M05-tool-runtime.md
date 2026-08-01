# M05：Tool SPI、Registry、Invoker 与权限

## 交付目标

把 `Tool`/`ToolRegistry` 升级为第三方可实现且统一校验、权限、超时、缓存和 trace 的 Tool runtime。本任务不迁移具体工具。

## 文件边界

- 新增：`backend/benchmark/tools/{__init__,protocol,registry,invoker,validation,capabilities,errors}.py`。
- 修改：`backend/services/agent/tools.py` 仅提供过渡 adapter。
- 禁止修改：`env_wrapper.py`、`trade_execution_tool.py`、memory/public API 工具。

## 暴露接口

实现公共规范的 `ToolSpec`、`ToolContext`、`ToolResult`、`Tool`、`ToolProvider`、`ToolInvoker`，并提供：

```python
class ToolRegistry:
    def register_provider(self, extension: ExtensionRef, provider: ToolProvider) -> None
    def get(self, name: str) -> RegisteredTool
    def list(self, capabilities: frozenset[str] | None = None) -> tuple[ToolSpec, ...]
    def freeze(self) -> None
```

## TODO

- [x] 校验 tool 名称 namespace、JSON Schema draft、timeout 范围和 side effect/capability 一致性。
- [x] 重复名称一律失败；仅保留旧 provider 返回 `namespace:name` 的 suffix normalization adapter，并在 M06 删除。
- [x] Invoker 固定执行 pipeline：权限、input schema、timeout、invoke、output schema、redaction、event。
- [x] `ToolInvoker.call()` 与 `Tool.invoke()` 都是同步接口；一个工具返回后 Agent 才执行下一步骤。
- [x] 返回 coroutine/awaitable 的第三方 Tool 视为 `ToolRuntimeError`，系统不自动 await，也不提供 async Tool adapter。
- [x] `READ_ONLY` 且 `cacheable=True` 才可使用 tool cache；cache key 包含 account id、round id、tool version、规范化参数。
- [x] Tool 返回业务失败用 `ToolResult`；schema/timeout/框架故障转换为稳定错误码。
- [x] trading.write 只授予 `core.execute_trade` adapter，外部 Tool 不得声明该 capability，除非系统管理员显式白名单。
- [x] active tool selection 作为 `ToolView` 过滤，不修改全局 registry。
- [x] 输出给 OpenAI 的 schema 由 runtime 生成，保证与 input schema 同源。

实现说明与验证结果见 [M05 实现报告](M05-implementation-report.md)。

## 验收

- 第三方 Tool 只 import public SPI 即可注册、被 Agent 调用并产生 trace。
- 输入/输出 schema、timeout、capability、cache hit/miss 全有单测。
- 同步调用顺序和 awaitable 拒绝行为有契约测试。
- Tool registry 不保存 DB session/account 的 closure。
- `core.execute_trade` 未授权时不可见且不可调用。

`timeout_seconds` 在同步 v1 SPI 中定义为合作式 deadline：Invoker 将 Tool 自身
timeout 与决策 deadline 中较早者写入 `ToolContext.deadline_at`，Provider 负责把
剩余时间传给自身的网络、数据库或子进程调用。Runtime 不创建后台线程，也不承诺
抢占或终止不遵守契约的第三方 Python 代码；返回后的 elapsed/deadline 检查用于产生
稳定超时结果并暴露契约违规。

## 前置与并行

前置 M01。M03/M07/M09/M11 可并行；M06 与 M04 依赖本任务。
