# M17 实现报告

## 交付范围

本分支已同步 `refactor/main` 的 Wave 2 实现，并在此基础上完成 M17：为开源扩展作者提供可独立使用的同步 SDK 测试工具、可被 Catalog 真实装载的示例、离线 CLI 和开发文档。实现没有修改核心交易、Agent 编排或生命周期逻辑。

## SDK 测试工具

`benchmark.testing` 现在提供：

- `build_fake_context()` 以及 Agent Build Context fixture，生成不可变、可序列化的公开 DTO；
- fake LLM、Market、Memory、Sandbox、Event Sink 和 Trade Gateway；Trade Gateway 只在内存中记录命令，并按账户和幂等键重放结果；
- `AgentCase`、`ToolCase`、`assert_agent_contract()`、`assert_tool_contract()` 和 `assert_prompt_contract()`。

契约断言覆盖公开 DTO 类型、trace/decision round 身份、JSON Schema、命名空间、能力声明、超时 deadline、Prompt hash、运行事件 secret redaction，以及同步 SPI 约束。Agent、Tool、Prompt Provider 或 fake Provider 返回 awaitable 时会明确失败。没有显式 Tool case 时，只自动探测 schema 可生成合法输入的只读 Tool，不执行写副作用。

## 示例与 CLI

新增四个真实可装载扩展：

- `examples/extensions/minimal-agent`
- `examples/extensions/read-only-tool`
- `examples/extensions/prompt-override`
- `examples/extensions/combined-extension`

新增 `alpha-arena extension validate|test|list`，并保留 `python -m benchmark.extensions.cli` 入口。`list` 只做本地 manifest、schema 和 Prompt index 检查，不启动 scheduler 或连接数据库。`backend/pyproject.toml` 增加 console script、测试可选依赖及 examples 的 wheel/sdist 包含规则。

文档位于 `docs/extensions/`，说明十分钟 Agent、只读 Tool、Prompt-only 扩展、契约测试、公开 SPI、SemVer/api_version 和 capability 规则。

## 验证

- M17 扩展契约测试和主线 examples/CLI 测试通过；
- Ruff check、Ruff format check、`compileall`、`git diff --check` 通过；
- 四个示例的 `validate`、`test` 和 `list` CLI smoke test 通过；
- wheel smoke test 已写入 `backend/tests/extensions/test_m17_sdk.py`，并确认 wheel 内包含四个 examples 目录及 `alpha-arena` entry point。

## WAVE 4 兼容入口收尾

生产决策及评测已切换到 Runtime/Catalog 入口，现已删除 M03/M06/M08 遗留的 factory/core/env_wrapper 兼容模块及旧 Prompt 常量模块；审计 Prompt 由内置 Prompt registry 提供。迁移对照需要的旧实现移至 `backend/tests/legacy_fixtures/`，仅由测试导入。内置 Agent/Tool/Prompt 迁移、交易链及 examples/SDK 回归通过，详见 [WAVE 4 收尾与验收报告](../wave4-implementation-report.md)。

## 2026-09-25 SDK 与默认测试收尾

补入此前被忽略的 `tests/testing/test_contracts.py`，使 awaitable provider 夹具先满足现行 SPI，字符串样例与当前 `example` 契约一致。pytest 默认排除 integration；外部 provider 或 MySQL 集成需显式 `-m integration`。完整 `tests` 和 `test` 目录均纳入最终回归，证据见 [整体完成核查](../overall-completion-audit.md)。
