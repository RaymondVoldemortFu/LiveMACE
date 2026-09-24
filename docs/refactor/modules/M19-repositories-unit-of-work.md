# M19：Repository 与 Unit of Work 边界

## 交付目标

为 application service 提供稳定持久化端口，消除 route/Agent/plugin 对 SQLAlchemy session 和 ORM 的直接依赖，不改变 schema 或查询结果。

## 文件边界

- 新增：`backend/benchmark/persistence/{uow,repositories,views}.py`。
- 修改：`backend/repositories/*.py`、`database/connection.py`。
- 各 service/route 的迁移由其所属任务完成；本任务提供接口和 SQLAlchemy adapter。

## 接口

```python
class UnitOfWork(Protocol):
    accounts: AccountRepository
    positions: PositionRepository
    orders: OrderRepository
    trades: TradeRepository
    decisions: DecisionRepository
    traces: TraceRepository
    snapshots: SnapshotRepository
    evaluations: EvaluationRepository
    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
```

当前 SQLAlchemy 和 Agent worker 都采用同步模型，因此 UoW 只提供同步 context manager。不得增加 async session 假象或在 repository 中切换线程。

## TODO

- [x] 逐域定义 repository protocol，仅返回 DTO/view 或受控 entity handle，不向公共扩展暴露 ORM。
- [x] 补齐 Trade/Decision/Trace/Snapshot/Evaluation repository。
- [x] SQLAlchemy UoW 拥有且关闭 session；repository 不自行 commit。
- [x] request、scheduler、decision worker、trade gateway 的 session scope 分别测试。
- [x] 每个账户 worker 独立创建和关闭 UoW；同一 UoW 不得跨线程复用。
- [x] 对账户配置并发更新、pending order 处理提供 row lock/乐观锁接口。
- [x] 保留 SQLite NullPool 与 MySQL pool 配置；连接创建不移入 repository。
- [x] import boundary test：repositories 不 import FastAPI、Agent、market provider、WebSocket。

## 验收

- UoW commit/rollback/close 在成功、业务拒绝、异常、取消下均有测试。
- application service 单测可用 in-memory fake repository，不启动数据库。
- repository 函数不调用 LLM/market/WS，不自行决定交易策略。
- 原有 repository 行为和查询排序保持。

## 实现约束：最小事务能力

- UnitOfWork 不公开 SQLAlchemy Session、Engine、Connection、Pool 或 Result，也不通过通用代理/属性黑名单模拟安全边界。
- SQLAlchemy Session 仅存在于 concrete UoW、repository adapter 与 infrastructure transaction adapter 内部；application service 只依赖 repository/transaction Protocol。
- 正常退出但未 commit、业务拒绝、异常与取消均显式 rollback；commit/rollback 后禁止隐式开启第二个事务。
- repository 公共结果逐步收敛为 DTO/view 或受控 handle；公共扩展永远不能取得 ORM/Session。

## 前置与并行

前置 M01。各 repository domain 可并行；M12 涉及账户配置表，需协调后串行合并 model 变更。

## 2026-09-25 边界收尾

Decision worker 输入通过 UoW factory 读取；账户选择与轮次摘要落库移入 persistence。Compliance service 接收 Account/RuleEvaluation/Trace/Decision repository；checkpoint application service 通过可注入 `CheckpointBatch` 协议协调，具体事务、savepoint 和关闭逻辑由 persistence 持有。事件 sink 的 DB 写入亦移入 persistence，application 保留纯脱敏逻辑。应用层不再创建 SQLAlchemy Session 或调用 query。

最终完整回归及真实环境证据见 [整体完成核查](../overall-completion-audit.md)。
