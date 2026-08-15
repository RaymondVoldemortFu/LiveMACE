# M11 实现报告

## 事务与错误边界

公共 `TradeCommandGateway` 是只暴露 `execute()` 的 runtime-checkable Protocol；`SynchronousTradeCommandGateway` 使用 `UnitOfWorkFactory` 为每个命令创建同步事务。Gateway 路径调用旧 `order_matching` 和 Crypto leverage executor 时显式关闭其内部 commit/rollback，由 UoW 对下列写入一次性提交：

- 账户现金与保证金；
- 持仓；
- 订单与成交；
- Agent 决策日志；
- 幂等 receipt。

预期业务拒绝返回带稳定 reject code 的 `TradeCommandResult`。未知异常、坏 executor 返回、数据损坏、UoW 缺失和幂等状态不确定均抛 `TradeGatewayError`；错误 details 不复制数据库或上游异常文本。

## 持久化幂等

新增 `trade_command_receipts`，对 `(account_id, idempotency_key)` 建立数据库唯一约束。命令在执行任何金融写入前先 claim receipt，业务结果与账本在同一个事务中提交。

- 同 key、同命令返回首个已提交结果；
- 同 key、不同命令抛 `TRADE_IDEMPOTENCY_KEY_REUSED`；
- 执行异常时金融写入和 PENDING receipt 一起回滚，可安全重试；
- 并发 Gateway/worker 由数据库唯一约束协调，不依赖单进程缓存保证正确性。
- Gateway 在 receipt 查询/claim 和金融读取之前获取账户行锁；同账户不同 idempotency key 按账户串行，MySQL integration test 验证第二个 executor 必须等待第一个事务提交。

`execute_trade_tool()` 不再生成随机 key；调用方必须提供 `idempotency_key`，或同时提供 `decision_round_id` 与 `tool_call_id`。缺失时返回 `IDEMPOTENCY_KEY_REQUIRED`，避免伪幂等。

Legacy Agent 过渡链路由编排层把 `decision_round_id` 显式传入 Agent，Agent 的统一 tool-dispatch 再把供应商返回的 `tool_call_id` 注入 `execute_trade_tool()`，形成 `{decision_round_id}:{tool_call_id}`。这些字段不出现在 LLM tool schema 中；模型若自行提交 `idempotency_key`、`decision_round_id` 或 `tool_call_id`，调用会以 `TOOL_RUNTIME_ARGUMENT_FORBIDDEN` 显式失败，不能覆盖运行时身份。M06 的 `core.execute_trade` 将直接从 `ToolContext` 取得同一组可信元数据。

所有 sizing value 在任何 Decimal 比较和 legacy clamp 前必须为有限数；NaN、正负 Infinity 与溢出为 Infinity 的指数输入统一以 `SIZING_VALUE_INVALID` 拒绝，不能退化为全仓开仓或全部平仓。

## 内部订单接口

Gateway 同时提供 `create_order()`、`cancel_order()` 和 `process_pending()`。这些接口使用相同 UoW，锁定账户、订单或 pending 集合，并继续复用现有普通 MARKET/LIMIT 匹配逻辑。Crypto 杠杆命令继续复用既有 executor。

## 测试系统

`backend/tests/trading/` 覆盖：

- 成功写入与 receipt 原子提交；
- 任意中途异常时现金、持仓、订单、成交、日志和 receipt 全量回滚；
- 业务拒绝持久化并可重复读取；
- 同 key 不同命令显式失败；
- 跨 Gateway 实例及并发线程仅执行一次；
- legacy Crypto 实际执行链的成功与故障注入；
- MARKET/LIMIT 创建、pending 成交和取消命令；
- 数据库启动 migration 的幂等性。
- `register_default_tools -> ReAct Agent -> execute_trade_tool -> Gateway` 生产调用链，以及缺失轮次和模型伪造运行时字段的失败行为。

M00 characterization 继续验证原有交易状态变化；HTTP/WS 调用点迁移仍由 M21 完成，`core.execute_trade` 的最终 Tool 包装由 M06 完成。
