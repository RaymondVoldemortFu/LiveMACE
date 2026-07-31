# M11：统一 Trade Command Gateway

## 交付目标

让 Agent tool、HTTP 和 WS 的交易写入共享同一个应用接口，同时完全保留普通订单、LIMIT、Crypto 杠杆、US 闭市和资金持仓语义。

## 文件边界

- 新增：`backend/benchmark/application/trading/{gateway,commands,policy,errors}.py`。
- 适配：`services/order_matching.py`、`order_executor_leverage.py`、`agent/trade_execution_tool.py`。
- 路由/WS 迁移由 M21 完成；本任务只提供 adapter 和 service tests。

## 暴露接口

实现公共规范 `TradeCommandGateway.execute()`。另设内部订单接口：

```python
def create_order(command: CreateOrderCommand) -> OrderCommandResult
def cancel_order(command: CancelOrderCommand) -> OrderCommandResult
def process_pending(command: ProcessPendingOrders) -> ProcessingResult
```

## TODO

- [x] 将 symbol/market、market status、price、operation、direction、sizing、leverage 校验归一为 policy，但不改变规则值。
- [x] Gateway 以 UoW 开启单个写事务；失败 rollback，返回结构化 reject。
- [x] Gateway 暴露同步接口并在返回前完成事务；Agent 工具必须等待结果，不存在后台延迟提交。
- [x] `idempotency_key` 在同一账户重复调用返回首个结果，不再次修改账本；设计持久化或事务内唯一约束方案。
- [x] 普通 MARKET/LIMIT 继续使用 `order_matching`，杠杆 Crypto 继续使用现有 executor；gateway 只编排。
- [x] `execute_trade_tool()` 缩为参数 adapter，最终在 M06 由 `core.execute_trade` 替换。
- [x] 映射当前 ValueError 到稳定 reject code，并保留用户可见 message。
- [x] 返回 order/trade ref、normalized command，不返回 ORM entity。

实现说明和验证证据见 [M11-implementation-report.md](M11-implementation-report.md)。

## 验收

- M00 全部交易状态断言通过。
- Tool/HTTP adapter 对同一 command 得到同一 normalized result。
- 重复 idempotency key 不产生第二笔订单或成交。
- Gateway 是 application 层唯一可以同时协调账户、持仓、订单、成交写入的入口。

## 前置与并行

前置 M00、M01；可与 Agent/Tool/Prompt runtime 并行。M06、M10、M21 依赖。
