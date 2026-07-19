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

- [ ] 逐域定义 repository protocol，仅返回 DTO/view 或受控 entity handle，不向公共扩展暴露 ORM。
- [ ] 补齐 Trade/Decision/Trace/Snapshot/Evaluation repository。
- [ ] SQLAlchemy UoW 拥有且关闭 session；repository 不自行 commit。
- [ ] request、scheduler、decision worker、trade gateway 的 session scope 分别测试。
- [ ] 每个账户 worker 独立创建和关闭 UoW；同一 UoW 不得跨线程复用。
- [ ] 对账户配置并发更新、pending order 处理提供 row lock/乐观锁接口。
- [ ] 保留 SQLite NullPool 与 MySQL pool 配置；连接创建不移入 repository。
- [ ] import boundary test：repositories 不 import FastAPI、Agent、market provider、WebSocket。

## 验收

- UoW commit/rollback/close 在成功、业务拒绝、异常、取消下均有测试。
- application service 单测可用 in-memory fake repository，不启动数据库。
- repository 函数不调用 LLM/market/WS，不自行决定交易策略。
- 原有 repository 行为和查询排序保持。

## 前置与并行

前置 M01。各 repository domain 可并行；M12 涉及账户配置表，需协调后串行合并 model 变更。
