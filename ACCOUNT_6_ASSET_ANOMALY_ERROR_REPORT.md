# Account 6 Asset Anomaly Error Report

## 摘要

本报告分析 `alpha_arena_final.sqlite` 中 account id `6`、name `gemini-3.1-pro-preview-react-tool` 的资产异常波动问题。

该账户初始资金为 `10000 USD`，但在模拟周期内 1d 资产快照最高达到约 `55893.89 USD`，决策日志中部分 `total_balance` 最高达到约 `59953.18 USD`。经核查，异常不是 5min 展示曲线计算 bug 导致，也不是正常市场价格波动导致，而是交易执行、强平、账户状态读取和资产口径混用共同造成的真实交易状态异常。

核心结论：

- 系统产生了大量 `LIQ-*` 强平订单，且绝大多数发生在正常开仓后的数秒内。
- `accounts.margin_used` 未随强平正确释放，最终残留到 `177050.22`，远超初始资金和实际持仓规模。
- `get_account_state` 在同一交易会话中返回陈旧状态，使 agent 误判“订单已成交但仓位没有出现”，随后重复开仓。
- 部分决策日志的 `total_balance` 使用了名义敞口口径，而不是权益口径，进一步放大了账户资产表现。

## 异常现象

账户基础状态：

- `id`: `6`
- `name`: `gemini-3.1-pro-preview-react-tool`
- `initial_capital`: `10000`
- `current_cash`: `208.82`
- `frozen_cash`: `0`
- `margin_used`: `177050.22`

资产快照概况：

- `1d` 快照最小资产约 `8304.44`，最大资产约 `55893.89`
- `1h` 快照最小资产约 `1224.28`，最大资产约 `29228.71`
- `5m` 曲线已知存在计算 bug，本报告未将其作为主要依据

交易和决策概况：

- `ai_decision_logs`: `455` 条
- `orders`: `494` 条，全部为 `FILLED`
- `trades`: `495` 条
- 总成交名义金额约 `1072077.23`
- 总 commission 约 `907.79`

最终状态中，账户几乎没有现金，且多数 crypto 仓位数量为 `0`，但 `margin_used` 仍为 `177050.22`。这说明保证金占用状态已经与实际仓位脱钩。

## 关键证据

### 1. 强平订单大量存在

数据库中 account 6 共有 `164` 笔订单号以 `LIQ-` 开头的强平订单：

- `LIQ-*` 强平成交名义金额约 `360195.34`
- `LIQ-*` 强平手续费约 `360.44`
- 强平时间范围：`2026-04-14 19:03:41` 到 `2026-05-13 07:03:37`

按 symbol 汇总，强平主要集中在：

- `BTC SELL`: `46` 笔，名义金额约 `114194.70`
- `SOL SELL`: `33` 笔，名义金额约 `69618.28`
- `ETH SELL`: `24` 笔，名义金额约 `53793.29`
- `BNB SELL`: `17` 笔，名义金额约 `41402.31`
- `DOGE SELL`: `12` 笔，名义金额约 `30157.09`

### 2. 强平几乎紧跟开仓发生

对 `LIQ-*` 订单与同 symbol 上一笔非强平订单做时间配对：

- `164` 笔强平中，`147` 笔发生在上一笔同 symbol 非强平成交后的 `5` 秒内
- `160` 笔发生在 `60` 秒内
- `5` 秒内强平对应的名义金额约 `323926.88`

这不是正常行情波动导致的逐步止损或爆仓，而是系统状态在开仓后立即触发强平。

典型样例：

- `2026-05-01 23:01:35`，ETH/SOL long 开仓成功，订单 `3949`、`3950`
- `2026-05-01 23:01:38`，系统生成 `LIQ-*` ETH/SOL sell 强平订单 `3951`、`3952`
- `2026-05-01 23:01:41`，agent 因为看到仓位未出现，重新开 ETH/SOL long，订单 `3953`、`3954`
- `2026-05-01 23:01:43`，系统再次生成 `LIQ-*` ETH/SOL sell 强平订单 `3955`、`3956`

### 3. Agent 被陈旧账户状态误导并重复开仓

在 trace `4de60eb7-3d1b-416a-88ea-7968b506bb88` 中：

1. agent 调用 `execute_trade` 开 ETH 和 SOL long，工具返回 `executed: true`
2. agent 立刻调用 `get_account_state`
3. `get_account_state` 返回的账户仍显示：
   - `cash`: `22298.67`
   - ETH quantity: `0.0`
   - SOL quantity: `0.0`
4. agent 因此写出原因：`Previous order filled but position not showing up, likely due to API delay or simulation quirk`
5. agent 再次执行 ETH/SOL long

在 trace `c1a2d76c-6234-4e7e-b6f1-94f920ea581d` 中，同类现象再次出现：

- BTC/ETH/SOL 开仓工具返回 `executed: true`
- 后续 `get_account_state` 仍报告 crypto quantity 为 `0`
- agent 明确总结：账户状态仍显示 crypto positions 为 `0`，但它认为这是 delay 或 simulation quirk

这说明异常交易行为不是单纯模型策略激进，而是工具返回和状态读取之间存在一致性问题，直接诱导 agent 重复开仓。

### 4. `margin_used` 未随强平释放

最终 `positions` 表显示，多数 crypto 杠杆仓位数量为 `0`，例如：

- `BTC LONG`: quantity `0`
- `ETH LONG`: quantity `0`
- `SOL LONG`: quantity `0`
- `DOGE LONG`: quantity `0`
- `BNB LONG`: quantity `0`
- `XRP LONG`: quantity `0`

但 `accounts.margin_used` 仍为 `177050.22`。

这与账户实际持仓不一致。强平逻辑创建 `LIQ-*` 订单后走了普通 `order_matching.check_and_execute_order()` 路径，而不是 crypto 杠杆专用的 `order_executor_leverage.place_and_execute_crypto()` 路径。因此强平可能完成了仓位数量清零和成交记录写入，但没有正确释放 `account.margin_used`。

### 5. 保证金检查使用了陈旧 `margin_used`

调度器中的保证金检查逻辑计算：

- `equity = account.current_cash + total_pnl`
- `margin_level = equity / account.margin_used`

当 `account.margin_used` 未释放并持续累积后，即使新开仓规模不大，账户也会因为分母异常巨大而马上低于维持保证金比例，触发强平。

这形成了反馈循环：

1. 开仓扣除保证金，增加 `margin_used`
2. 保证金检查使用已经异常累积的 `margin_used`
3. 系统立即强平
4. 强平没有正确释放 `margin_used`
5. 下一次开仓更容易触发强平
6. agent 又因为陈旧状态读取而重复开仓

### 6. 决策日志资产口径被名义敞口放大

部分 tool-mode 决策日志的 `total_balance` 来自 `trade_execution_tool._calc_total_assets()`，该函数使用：

```python
return float(account.current_cash) + float(calc_positions_value(db, account_id))
```

其中 `calc_positions_value()` 返回的是名义价值或敞口，不是权益：

```python
total += price * Decimal(str(p.quantity)) * Decimal(str(p.leverage))
```

这会把杠杆仓位按 `quantity * price * leverage` 计入资产，使 `ai_decision_logs.total_balance` 被放大。对 account 6，最高的决策日志 `total_balance` 达到约 `59953.18`。

因此，决策轨迹里的资产值本身也混入了名义敞口口径，不能等同于账户权益。

## 根因判断

本次异常不是单一 bug，而是多个问题叠加：

1. **强平路径错误或不完整**
   - `LIQ-*` 订单通过普通订单撮合逻辑执行。
   - 对 crypto 杠杆仓位而言，该路径没有完整处理保证金释放、杠杆仓位结算和 side 语义。

2. **`margin_used` 状态污染**
   - 杠杆仓位关闭或强平后，`accounts.margin_used` 没有被正确扣减。
   - 最终账户无对应活跃仓位，但 `margin_used` 高达 `177050.22`。

3. **账户状态读取陈旧**
   - 同一 trace 内，交易工具返回已成交后，`get_account_state` 仍返回旧 cash 和旧 positions。
   - agent 基于该状态误判订单未反映，触发重复开仓。

4. **资产口径混用**
   - 权益计算应使用 `calc_positions_market_value()`。
   - 决策日志部分路径使用 `calc_positions_value()`，导致名义敞口被当作资产。

5. **强平与 agent 决策之间缺少清晰反馈**
   - `LIQ-*` 强平订单不是普通 ai decision 的 order_id。
   - agent 后续只能看到仓位消失，无法明确知道刚刚发生了强平，因此会误以为是状态延迟或模拟器异常。

## 影响

该问题会导致：

- 账户资产曲线出现远超初始资金的异常波动
- 模型决策历史中的 `total_balance` 被污染
- agent 重复开仓、反复被强平
- `margin_used` 持续累积，后续任何杠杆开仓都更容易被错误强平
- 资产、现金、仓位、保证金四者之间失去一致性

因此，该账户后半段模拟结果不应作为有效策略表现样本直接纳入评估。

## 建议修复

优先级从高到低：

1. **修复 crypto 杠杆强平执行路径**
   - 强平 crypto 杠杆仓位时应使用与正常平仓一致的杠杆结算逻辑。
   - 必须释放对应 `margin_used`。
   - 必须正确记录 PnL、手续费、利息和仓位 side。

2. **重建 `margin_used` 不变量**
   - `account.margin_used` 应等于当前所有活跃杠杆仓位的 entry margin 之和。
   - 可以增加校验任务，发现 `margin_used` 与 positions 不一致时直接报错，而不是继续交易。

3. **修复 `get_account_state` 会话一致性**
   - `execute_trade` commit 后，后续工具读取必须看到最新 DB 状态。
   - 若同一 SQLAlchemy session 存在 identity map 缓存，应在工具调用间 refresh / expire，或使用新的只读 session。

4. **统一资产口径**
   - 账户权益、决策日志 `total_balance`、历史决策工具和资产快照应统一使用权益口径。
   - 不应把 `calc_positions_value()` 的名义敞口当作资产。
   - 对杠杆仓位应使用 `calc_positions_market_value()` 或等价的 margin + unrealized PnL 口径。

5. **在 agent 可见状态中暴露强平事件**
   - `get_history_decisions` 或 `get_account_state` 应能显示近期 `LIQ-*` 强平事件。
   - 避免 agent 把强平后的仓位消失误判为“成交未反映”。

6. **增加回归测试**
   - 开 2x crypto long 后立即强平，应验证：
     - position quantity 归零
     - `margin_used` 释放到正确值
     - cash 按 margin + PnL - fee 更新
     - 后续 `get_account_state` 能看到最新状态
   - 连续开仓、强平、再开仓不应导致 `margin_used` 累积污染。

## 结论

account 6 的 50000 USD 级别资产异常波动，根源是强平和账户状态一致性问题，而不是正常策略收益或单纯展示曲线 bug。

最关键的错误链路是：

```text
crypto 杠杆开仓成功
→ 保证金检查使用异常累积的 margin_used
→ 系统立刻生成 LIQ-* 强平单
→ 强平未正确释放 margin_used
→ get_account_state 返回陈旧仓位/现金
→ agent 误判成交未反映并重复开仓
→ 强平循环继续，资产和决策日志被污染
```

在修复上述问题前，建议将 account 6 的模拟结果标记为无效或异常样本，不用于模型交易能力评估。
