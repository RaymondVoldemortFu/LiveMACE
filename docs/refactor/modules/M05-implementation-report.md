# M05 Tool SPI、Registry 与 Invoker 实现报告

## 交付结果

M05 已建立公开的同步 Tool Runtime。第三方扩展可以只依赖
`benchmark.tools` 和 `benchmark.contracts` 定义 Tool、注册 ToolProvider，并通过
统一 Invoker 执行。现有内置 Agent 和工具仍走原调用链，具体工具迁移留给 M06。

## 公开接口

`benchmark.tools` 公开：

- `Tool`、`ToolProvider`、`ToolInvoker` 同步 Protocol；
- `ToolRegistry`、`ToolView`、`SynchronousToolInvoker`；
- `RegisteredTool`、`ToolRuntimeEvent`、`ToolEventSink`；
- `ToolSpec`、`ToolContext`、`ToolResult`、`SideEffect` 的稳定 re-export；
- Registry、Runtime 和公共组件错误。

M03 的 `AgentBuildContext.tools` 已由 `Any` 收紧为公开 `ToolInvoker`，完成 G2
与 G3 的 Wave 1 接口衔接。

第三方 Tool 的最小形态如下：

```python
from benchmark.tools import SideEffect, ToolResult, ToolSpec


class SentimentTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="com.example.sentiment",
            description="Read sentiment for one symbol.",
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
            output_schema={"type": "object"},
            side_effect=SideEffect.EXTERNAL_READ,
            required_capabilities=("network.read",),
        )

    def invoke(self, context, arguments) -> ToolResult:
        return ToolResult(ok=True, value={"score": 0.5})
```

## Registry 与权限边界

- Registry 在 bootstrap 阶段注册 ToolProvider，按全局 Tool 名称判重；注册批次
  失败时不会留下部分条目。
- Tool 名称必须是小写 ASCII namespaced identifier；输入和输出 schema 使用匹配
  schema draft 的 `jsonschema` validator 检查。
- timeout 上限、副作用和 capability 组合在注册时校验。
- `core.*` namespace 默认只允许 `benchmark.core` 内置扩展注册。
- `trading.write` 默认只允许 `core.execute_trade`。管理员可以在构建 Registry 时
  显式加入额外的完整 Tool 名称。
- Registry freeze 幂等；冻结后只读。账户 capability 和 active Tool 选择保存在
  不可变 `ToolView` 中，不修改全局状态。

## 同步调用 pipeline

`SynchronousToolInvoker.call()` 固定执行：

```text
Registry/View 解析
  -> capability 与 active selection
  -> input JSON Schema
  -> decision deadline
  -> READ_ONLY cache lookup
     -> hit: tool.cache_hit -> return
     -> miss: tool.started
  -> 同步 Tool.invoke()
  -> awaitable/类型/elapsed timeout 检查
  -> output JSON Schema
  -> READ_ONLY cache write
  -> redacted ToolRuntimeEvent
```

Tool 在调用方账户 worker 线程内直接执行。Runtime 不创建 event loop、asyncio
task 或额外 Agent worker，也不使用会让超时 Tool 在后台继续产生副作用的抢占式
线程。Invoker 将 Tool timeout 与决策 deadline 中较早者作为
`ToolContext.deadline_at` 传给 Provider；Provider 负责将剩余时间落实到底层 I/O。
Runtime 不抢占不遵守该合作式契约的第三方 Python 代码。只读/外部读取 Tool 完成后若已超过有效 deadline，返回稳定的
`TOOL_TIMEOUT`。写副作用 Tool 已经同步完成时必须保留真实 `ToolResult`，并通过
`timeout_exceeded`、`timeout_seconds`、`elapsed_seconds` metadata 记录超时，避免
把已发生的 memory/sandbox/trading 写入伪装成可重试失败。

业务拒绝继续使用 `ToolResult(ok=False)`。awaitable、坏返回类型、坏输出 schema
和未处理异常归一化为 `ToolRuntimeError`，进程级异常不包装。

## Cache、schema 与事件

- 只有 `READ_ONLY` 且 `cacheable=True` 的成功结果写入 cache。
- cache namespace 包含 account id、extension id/version 和 Tool name；round id 与
  规范化参数一并进入 cache key。同一账户仍可复用，同一 decision round 内不同账户
  不共享结果。早期受控内置工具的跨账户复用行为在开放 Tool 集合后停止。
- OpenAI function schema 直接从 `ToolSpec.input_schema` 生成，没有第二份参数定义。
- Tool 事件携带 account、trace、round、call 和完整 `ExtensionRef`；未解析到 Tool
  的 denied 事件 component 为 `None`。常见 credential/header 字段与 URL userinfo
  在进入事件前脱敏，Agent 收到的业务结果不被修改。
- cache 运行期读写故障降级为 miss，不伪造成功结果。
- Redis cache 配置有限 connect/read socket timeout；lookup 结束后重新检查 decision
  deadline，已经过期的 cache value 不产生 `tool.cache_hit`。

## Legacy 边界

`services/agent/tools.py` 保留原 `Tool`、`ToolRegistry`、active selection 和
`namespace:name` suffix 行为，并增加显式 `LegacyToolAdapter`/
`LegacyToolProviderAdapter`。旧 `ToolRegistry.call()` 是明确的 `ToolInvoker`
bridge，将公开 `core.*` 名称映射到旧 callable Tool，并返回公共 `ToolResult`。
因此 M05 不改变内置 Agent 行为，也不提前修改 `env_wrapper.py`；这些过渡类型和
bridge 由 M06 删除。

## 测试覆盖

`backend/tests/tools/` 覆盖：

- 第三方 provider 注册、冲突原子性、freeze 并发读取；
- namespace、JSON Schema、timeout、副作用/capability、trading whitelist；
- ToolView capability 和 active selection 隔离；
- 同线程同步调用、输入/输出校验、业务失败、框架异常、awaitable 拒绝；
- cache hit/miss、account/round/version/参数隔离、只读缓存限制；
- cache hit 不产生虚假的 `tool.started`，写副作用 elapsed timeout 保留真实结果；
- timeout、OpenAI schema 同源、事件脱敏和公开 import boundary；
- legacy adapter 行为。

最终验证结果：

- `uv run pytest tests/tools tests/agents tests/characterization -q`：81 passed；
- `uv run pytest tests -q --ignore=tests/trading_commands_immediate_execution_test.py`：
  177 passed；
- 旧 Tool/ToolSelector 定向回归：11 passed；
- scoped Ruff、compileall、`git diff --check` 和 wheel 内容检查通过。

未过滤的 `backend/tests` 仍有一个与 M05 无关的旧 M10 测试失败：该测试尝试
monkeypatch 已按重构要求删除的 `AgentConfig.USE_AGENT`。M05 没有恢复 Legacy
开关，也没有越界修改该测试。
