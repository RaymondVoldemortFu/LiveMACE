# M17：扩展 SDK、样例与契约测试套件

## 交付目标

交付开源用户真正可使用的开发表面：最小 Agent、Tool、Prompt-only 示例，CLI 校验和无需启动完整交易系统的契约测试。

## 文件边界

- 新增：`examples/extensions/`、`backend/benchmark/testing/`、`docs/extensions/`。
- 修改：README 增加入口链接；`pyproject.toml` 增加可选 test 依赖/CLI entry。
- 不修改核心运行逻辑。

## 样例

```text
examples/extensions/minimal-agent/
examples/extensions/read-only-tool/
examples/extensions/prompt-override/
examples/extensions/combined-extension/
```

样例必须是可被真实 catalog 装载的完整目录，不是伪代码。

## 测试 API

```python
def assert_agent_contract(factory: AgentFactory, cases: Sequence[AgentCase]) -> None
def assert_tool_contract(provider: ToolProvider, cases: Sequence[ToolCase]) -> None
def assert_prompt_contract(provider: PromptProvider) -> None
def build_fake_context(...) -> DecisionContext
```

## TODO

- [x] 编写“十分钟创建 Agent”“新增只读 Tool”“只覆盖 Prompt”教程。
- [x] 提供 fake LLM、market、memory、sandbox、trade gateway；fake trade 默认不修改真实 DB。
- [x] 契约测试验证返回类型、context id、schema、timeout、namespace、secret redaction。
- [x] Agent、Tool、Provider 示例全部使用同步 SPI；增加返回 coroutine/awaitable 必须失败的契约测试。
- [x] 文档说明外部 Agent 可以在自己的同步 `run()` 内自行管理 asyncio/线程池，但系统不提供异步兼容、资源管理或正确性保证。
- [x] CLI `alpha-arena extension validate/test/list`；`list` 可离线列目录内容，不启动 scheduler。
- [x] 记录 public SPI import 清单、SemVer/api_version 兼容规则和升级示例。
- [x] 增加 wheel 安装后从临时目录装载 examples 的 smoke test。
- [x] 删除 M03/M06/M08 留下的生产 deprecated compatibility re-export；迁移对照 helper 归入 `backend/tests/legacy_fixtures/`，详见 [WAVE 4 验收报告](../wave4-implementation-report.md)。

## 验收

- 在全新 virtualenv 安装项目后，四个样例可通过 validate 和 contract tests。
- 用户教程不引用 `backend/services`、ORM、FastAPI 或内部文件修改。
- Prompt-only 示例零 Python 代码。
- read-only Tool 无法获得 trading.write capability。

## 前置与并行

前置 M04、M06、M08、M13。样例可三人并行，testing API 先冻结。
