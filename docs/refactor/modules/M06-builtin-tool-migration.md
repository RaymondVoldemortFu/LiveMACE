# M06：内置工具包拆分与迁移

## 交付目标

将 `env_wrapper.register_default_tools()` 的大函数拆成可列举的内置 ToolProvider，同时保持工具名称、参数、返回内容和副作用。

## 文件边界

- 主改：`backend/services/agent/env_wrapper.py`、`memory_tools.py`、`history_tool.py`、`public_apis_registry.py`、`sub_agents/search_agent.py`。
- 新增：`backend/benchmark/builtin/tools/{market,account,history,search,sandbox,memory,trading,public_api}.py`。
- 不修改：交易底层函数、行情 provider、Prompt 文案。

## ToolProvider 拆分

| provider id | 工具范围 | side effect |
| --- | --- | --- |
| `core.market-tools` | snapshot、price、kline | read_only/external_read |
| `core.account-tools` | account/position/order state、history | read_only |
| `core.search-tools` | search sub-agent/public APIs | external_read |
| `core.sandbox-tools` | shell/file/python、Kline file | sandbox_write |
| `core.memory-tools` | memory add/search | memory_write/read_only |
| `core.trading-tools` | execute_trade | trading_write |

## TODO

- [ ] 逐个记录现有工具 name、description、parameters 和返回 fixture，禁止同时改文案。
- [ ] 所有内置工具保持同步调用；不得在迁移中引入 async Tool 或改变 Agent 等待工具结果的顺序。
- [ ] 工具实现通过 `ToolContext` 获取 account/round/trace；所需 service 通过构造参数注入。
- [ ] 删除 lambda/closure 中捕获 SQLAlchemy session 的方式。
- [ ] `core.execute_trade` 仅调用 `TradeCommandGateway`，不直接 import 两套 executor。
- [ ] memory disabled 时由账户 toolset resolution 排除 provider，不在工具内临时判断。
- [ ] public API 工具保留动态 schema 和 limit，但 namespace 化并报告来源。
- [ ] sandbox Kline 文件名、row_count 和 source 写入 metadata；写失败返回 ToolResult error。
- [x] 删除 `register_default_tools()` 的生产调用；对照 helper 位于 `tests/legacy_fixtures/tools.py`。

## 验收

- 当前默认账户解析出的工具集合与迁移前等价；名称变化只允许文档明确的 `core.` namespace，并提供一次性账户配置迁移。
- `test_tool_use_dynamic_schema`、round-trip、cache、edge case、execute_trade 测试通过。
- 内置工具模块不 import FastAPI；除 trading adapter 外不 import交易执行模块。
- 每个 provider 可单独构造和测试。
- 工具完成前调用线程阻塞，工具返回后 Agent 才进入下一次 LLM 调用。

## 前置与并行

前置 M05、M09、M11。六个 provider 可并行迁移，`env_wrapper.py` 清理由集成人员最后完成。
