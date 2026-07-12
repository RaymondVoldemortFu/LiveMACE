# M05：Tool SPI、Registry、Invoker 与权限

## 交付目标

把 `Tool`/`ToolRegistry` 升级为第三方可实现且统一校验、权限、超时、缓存和 trace 的 Tool runtime。本任务不迁移具体工具。

## 文件边界

- 新增：`backend/alpha_arena/tools/{__init__,protocol,registry,invoker,validation,capabilities,errors}.py`。
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

- [ ] 校验 tool 名称 namespace、JSON Schema draft、timeout 范围和 side effect/capability 一致性。
- [ ] 重复名称一律失败；仅保留旧 provider 返回 `namespace:name` 的 suffix normalization adapter，并在 M06 删除。
- [ ] Invoker 固定执行 pipeline：权限、input schema、timeout、invoke、output schema、redaction、event。
- [ ] `READ_ONLY` 且 `cacheable=True` 才可使用 tool cache；cache key 包含 round id、tool version、规范化参数。
- [ ] Tool 返回业务失败用 `ToolResult`；schema/timeout/框架故障转换为稳定错误码。
- [ ] trading.write 只授予 `core.execute_trade` adapter，外部 Tool 不得声明该 capability，除非系统管理员显式白名单。
- [ ] active tool selection 作为 `ToolView` 过滤，不修改全局 registry。
- [ ] 输出给 OpenAI 的 schema 由 runtime 生成，保证与 input schema 同源。

## 验收

- 第三方 Tool 只 import public SPI 即可注册、被 Agent 调用并产生 trace。
- 输入/输出 schema、timeout、capability、cache hit/miss 全有单测。
- Tool registry 不保存 DB session/account 的 closure。
- `core.execute_trade` 未授权时不可见且不可调用。

## 前置与并行

前置 M01。M03/M07/M09/M11 可并行；M06 与 M04 依赖本任务。

