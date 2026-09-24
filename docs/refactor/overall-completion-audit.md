# 整体重构完成度核查

日期：2026-09-25。核查基准：M00–M23、模块 README 完成定义及 RFC-0010。结论：主要功能已落地，但仍有明确缺项，尚不能将整个重构标记为完成。

本记录补充并限定 WAVE 4 验收报告的完成结论。此前通过的测试和四个 bug 修复仍成立；这些证据不足以证明全部原始架构要求均已满足。

## 已确认的剩余工作

| 范围 | 缺项及证据 | 完成条件 |
| --- | --- | --- |
| M12/M13/M15：具名工具集合 | `modules/README.md:139` 要求账户选择 `toolset_ids`；`backend/benchmark/extensions/catalog.py:207` 对任意非空集合返回 `TOOLSET_NOT_FOUND`。当前 UI capability 分组只展开为 `disabled_tools`。 | 实现具名集合定义、校验、保存和运行时解析，并覆盖下一轮工具选择。 |
| M11：交易计算分层 | `modules/M11-trade-command-gateway.md:47` 要求 plan/apply 分离；`backend/benchmark/persistence/trade_transactions.py:46` 仍调用 legacy executor，由其直接进行 ORM 查询和写入。 | 将计算改为不访问数据库的 planner，ledger writer 通过 repository 应用计划，receipt 与账本由外层 UoW 一次提交。 |
| M19：应用层持久化边界 | `modules/M19-repositories-unit-of-work.md:54` 规定 application service 只依赖 repository/transaction Protocol。`application/decisions/selection.py:22` 直接创建 Session 并查询；`application/evaluation/checkpoint.py:38`、`:62` 直接使用 Session/commit；compliance service 构造函数仍接受 Session。 | 将具体 Session 操作归入 persistence/infrastructure，应用服务使用可替换的协议。 |
| M21/M23：API 类型覆盖 | 实际生成 OpenAPI 后，17 个 `/api` 端点的成功 JSON schema 为空，包含 memory、ranking、account overview、agent latest 等。`frontend/app/lib/api/memory.ts` 仍返回 `Promise<any>`。 | 为尚未声明的 API 补齐实际 DTO，前端消费生成类型，契约检查覆盖所有相关业务端点。 |
| M17：SDK 契约回归 | `backend/tests/testing/test_contracts.py:155`、`:164` 两项失败，单独运行可复现；一项期待 awaitable 错误却提前得到 SPI 错误，一项期待字符串样例 `symbol` 而实现返回 `example`。 | 核对现行 SDK 契约与旧测试夹具并对齐，恢复完整 SDK 测试通过。不能仅据此认定运行时有两项生产故障。 |
| M16：事件日志关联 | 任务要求结构化日志与 DB event 使用同一 event id；`application/decisions/observability.py:61` 仅为 DB 行创建 UUID，成功不输出对应结构化事件日志，失败 warning 也没有该 event id。 | 统一生成事件身份，并用于持久化和结构化日志，验证持久化失败时仍可定位事件。 |
| RFC-0010：默认测试隔离 | `backend/pyproject.toml:87` 只声明 marker，没有默认排除 integration；`backend/test/test_grok_openai_compat_integration.py` 中的测试会加载凭据并发起模型请求。当前必须手动传 `-m 'not integration'`。 | 默认测试命令排除真实外部 provider，显式开启集成测试；同步文档。此项属于全局 RFC 开发验收范围。 |

以上按实际实现或运行结果确认，没有将旧任务文档未勾选的复选框直接视作代码未完成。M11 Gateway 的事务和幂等外壳已实现，worker 读 UoW 与 Gateway 写 UoW 也已分离；M11 的剩余工作是最终计算层分离。

## 本次自动化结果

在 backend 目录执行：

```sh
DATABASE_URL=sqlite:// .venv/bin/python -m pytest \
  tests/accounts tests/bootstrap tests/persistence tests/providers tests/refactor \
  tests/testing tests/test_wave3_*.py \
  tests/services/test_market_data_tool_cache.py tests/services/test_tool_cache_rounds.py \
  -m 'not integration' -q --tb=short
```

结果：451 passed、2 failed。失败均位于 `tests/testing/test_contracts.py`。单独运行该文件结果为 6 passed、2 failed。

此前 WAVE 4 验收为后端 474 个不同用例通过，前端 11 项、类型检查与构建通过；本轮补查的是此前未覆盖的基础设施、SDK 基础测试及 WAVE 3 回归。不能将此前的通过数量称为整个仓库完整测试全绿。

OpenAPI 核查只装配 `create_app(mode=StartupMode.NO_BACKGROUND).openapi()`，未启动应用生命周期。空成功 JSON schema 共 19 个，其中 2 个为静态页面路由，不计入上述 17 个 API 端点。

沿用同一位子智能体独立核查 M00–M13；其确认具名工具集合和 M11 分层两项缺口。未新增子智能体，未操作浏览器或交易栈，未调用外部模型。本轮仅核查及记录结论，未修改生产代码。
