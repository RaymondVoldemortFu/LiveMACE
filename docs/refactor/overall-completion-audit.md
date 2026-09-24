# 整体重构完成度核查

日期：2026-09-25。核查基准：M00–M23、模块 README 完成定义及 RFC-0010。结论：本次确认的剩余缺口已全部关闭，整体重构任务完成。

## 收尾实现

| 范围 | 完成内容 | 验证 |
| --- | --- | --- |
| M12/M13/M15 具名工具集合 | Catalog 提供具名集合，选择取并集后应用禁用项；保存 `toolset_ids`，worker 解析为实际工具。空选择保留兼容行为，未知集合拒绝。 | 四种 Agent 的 HTTP 校验/保存 → worker 工具列表；集合并集、禁用和能力检查。 |
| M11 交易分层 | Detached planner 负责开平仓、费用利息、sizing、普通成交、创建取消和冻结释放；ledger repository 负责应用计划；receipt 和账本由 Gateway UoW 一次提交。 | M00 数值特征、交易与 margin 回归、失败回滚、真实 MySQL 行锁测试。 |
| M19 持久化边界 | Decision 输入使用 UoW factory，selection/summary 写入移入 persistence；compliance 注入 repositories；checkpoint 使用 batch Protocol；application 不再直接管理 Session/query。 | 默认与注入 worker factory 均覆盖连接释放；成功、拒绝、取消和异常事务测试。 |
| M21/M23 类型契约 | 补齐 memory、ranking、overview、latest trace、健康检查等成功响应 DTO；生成前端类型并接入领域 clients。 | 所有 `/api` 成功 JSON 均声明非空 schema；生成文件漂移检查与前端类型检查。 |
| M17 SDK | 补入被忽略的 SDK 测试文件，更新过时 provider 夹具与样例断言；移除全局模块替换造成的测试污染。 | 完整 `tests` 和 `test` 两个目录回归。 |
| M16 可观测性 | 日志/DB 共用 event id，日志载荷脱敏，持久化失败仍保留关联 id。 | 事件身份和凭据脱敏回归。 |
| RFC-0010 默认测试隔离 | pytest 默认排除 integration，显式 `-m integration` 才访问外部依赖。 | 默认完整测试与独立真实验收分开运行。 |

真实验收额外暴露并修复了默认 worker UoW factory 的装配错误：此前测试均注入 factory，遗漏了生产默认路径。现默认延迟构造 factory，并以参数化回归同时覆盖两种路径。真实并发还发现 ContainerService 首次初始化竞态：单例过早标记为 ready，另一个 worker 可取得半初始化对象。现完整初始化持锁，最后发布 ready，并以受阻 Docker client 的双线程测试覆盖。

## 可重复执行

仓库根目录：

```sh
pnpm run build:frontend
pnpm --dir frontend test
git diff --check
```

backend 目录：

```sh
DATABASE_URL=sqlite:// .venv/bin/python -m pytest tests test -q --tb=short
DATABASE_URL=sqlite:// .venv/bin/python scripts/generate_frontend_types.py --check
.venv/bin/python scripts/refactor_acceptance.py --serve-after
```

真实验收脚本默认从 `.wave3/runtime.env` 读取 provider 配置，新建专属 MySQL/Redis 容器与临时账户；业务库与会重置 schema 的 MySQL 集成测试库分开。脚本运行真实模型、Agent、工具和 Gateway，自动核对事件、摘要、结果/receipt/成交关联，以及现金、保证金和持仓的独立复算；四个 Agent 可自行选择 HOLD，但整个验收要求至少一笔真实成交。结果保存在 `.refactor-acceptance/<时间>/`，凭据脱敏。调度器保持停止，结束后清理本次创建的容器。

`--serve-after` 只在自动验收全部成功后开放临时 HTTP 服务，供一次浏览器检查；退出服务会清理资源。

## 最终验收记录

完整后端回归：1030 passed，8 integration deselected。前端：11 passed，类型检查与生产构建通过。OpenAPI 生成类型漂移检查、新增 planner/ledger、验收脚本及相关回归的 Ruff 检查与 `git diff --check` 通过。

最终真实验收目录：`.refactor-acceptance/20260925-015015/`，轮次 `d543a119-a93a-4b48-8a93-feb6e04d98b2`，模型 `deepseek-flash`。自动验收于北京时间 2026-09-25 01:54:30 完成。

| Agent | 结束原因 | LLM 完成事件 | 成交 | 现金 | 已用保证金 |
| --- | --- | ---: | ---: | ---: | ---: |
| ReAct | trade_done | 8 | 1 | 9248.94 | 750.01 |
| MultiAgent | trade_done | 23 | 1 | 7998.59 | 0.00 |
| AdvancedMultiAgent | trade_done | 17 | 1 | 9199.44 | 0.00 |
| RuleAware | trade_done | 7 | 3 | 5496.88 | 0.00 |

四个独立测试账户各以 10000 初始资金运行，processed_accounts=4，errors={}；合计 55 条 LLM 完成事件、6 笔成交，均写入隔离测试账本。Agent 自行分析和选择交易，不使用固定模型输出。此前测试轮也产生过美股成交；最终通过轮次的成交为 Crypto。

- 自动检查通过：默认启动/readiness、具名配置保存、prompt provenance、唯一 run.result、摘要、回执/成交关联、资金/保证金/持仓独立复算、HTTP 读取、WS bootstrap/switch/snapshot。真实 MySQL 并发测试 2 passed。
- 全程复用同一独立 reviewer，按开发—审查—修复循环处理；最终 reviewer 再次离线重放四份 ledger，全部通过，事件与报告一致，未发现剩余阻断问题。
- Agent 和自动验收全部结束后，仅进行一次浏览器检查：打开临时首页并确认导航、控件和模型收益曲线正常渲染，随后关闭页面。
- 临时 HTTP 服务、专属 MySQL/Redis 和沙箱容器已清理；最终 `docker ps` 无运行容器，既有 `alpha-arena-wave3` 栈保持停止。

`report.json`、`round.json`、四组事件与账本 JSON 为本次验收证据。`database.sql.gz` 是核验前的数据库备份，位于受限本地目录，用于排查校验问题；不纳入版本库。后端完整测试、前端测试/构建日志亦存入同一目录。
