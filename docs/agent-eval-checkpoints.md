# Agent 周期结算评分（Checkpoint）实现说明

> 目标：对每个 agent（对应一个 AI 交易账户 Account）在固定结算周期内的表现进行“结算打分”，并把每个周期的结果像 checkpoint 一样落库保存，以支持：
>
>- 同一个 agent 在不同时间段纵向对比
>- 不同 agent 在同一时间段横向对比（公平）
>
> 当前实现采用 **固定时间切片结算 + 资金滚动** 的模式：
>
>- 实验开始时所有 agent 初始条件一致（初始资金、交易成本/约束一致）
>- 后续资金允许滚动，因此每个周期的期初资金可能不同
>- 打分同时提供：
>  - **绝对利润 PnL**（核心“赚了多少钱”）
>  - **归一化收益 return_rate**（核心“在各自资金规模下赚了多少比例”）

---

## 1. 实现概览（数据流）

整体链路：

1. APScheduler 定时触发 checkpoint job（轮询）
2. job 计算当前周期边界（对齐周期 end）
3. 对每个 AI account：
   - 计算期末净值 equity_end（cash + 仓位 equity，含未实现浮盈亏）
   - 期初净值 equity_start：取上个 checkpoint 的 equity_end（没有则用 initial_capital）
   - 得到 pnl 与 return_rate
4. 将结果写入数据库表 `agent_period_checkpoints`
5. 后端提供查询 API（leaderboard / account checkpoints / compare）
6. 前端在“LiveMACE bench”右侧新增卡片展示：
   - 最新周期排行榜
   - 选择 agent 查看其 checkpoints 列表

---

## 2. 核心设计要点

### 2.1 固定时间切片（结算周期）

- 结算周期为固定长度 `interval_seconds`（默认 3600 秒，即 1h）
- 结算窗口为：

```
period_end = floor(now.timestamp / interval_seconds) * interval_seconds
period_start = period_end - interval_seconds
```

- 这样可以把所有 agent 的结算窗口对齐到同一批周期边界，方便横向对比。

### 2.2 幂等落库（像 checkpoint 一样）

- 每个 agent 每个周期只允许写入一次。
- 表层面做唯一约束：

```
Unique(account_id, interval_seconds, period_end)
```

- job 可以高频轮询（例如每 30s 跑一次），但只有跨越新的 `period_end` 时才会插入新记录。

### 2.3 PnL 口径：已实现 + 未实现浮盈亏

本实现使用“净值 equity”来定义周期收益：

- `equity = current_cash + positions_equity`
- `positions_equity` 使用后端已有的 **仓位 equity** 计算方法（而不是名义仓位 notional），从而能把杠杆仓位按“保证金 + 未实现盈亏”计入净值。

周期指标：

- `pnl = equity_end - equity_start`
- `return_rate = pnl / equity_start`（equity_start 为 0 时返回 0）

---

## 3. 后端实现

### 3.1 数据模型（DB 表）

文件：`backend/database/models.py`

新增模型：`AgentPeriodCheckpoint`

字段说明：

- `account_id`：对应 agent 的 account
- `interval_seconds`：周期长度（秒）
- `period_start` / `period_end`：周期边界（datetime）
- `equity_start` / `equity_end`：期初/期末净值（DECIMAL）
- `pnl`：周期利润（DECIMAL）
- `return_rate`：周期收益率（Float）

唯一约束：`(account_id, interval_seconds, period_end)`

> 注：目前 DB 是 SQLite（`backend/database/connection.py`），datetime 存储为无时区 datetime。

### 3.2 计算与写入服务

文件：`backend/services/evaluation/checkpoint_service.py`

关键函数：

- `align_to_interval_end(now, interval_seconds)`
  - 将当前时间对齐到周期边界，得到 `period_start/end`

- `compute_account_equity(db, account)`
  - 计算 `equity = cash + positions_equity`
  - positions_equity 使用：`services.asset_calculator.calc_positions_market_value`

- `create_checkpoint_if_due(db, account, interval_seconds, now=None)`
  - 幂等：先查是否已存在 `(account_id, interval_seconds, period_end)`
  - `equity_start`：取上一条 checkpoint 的 `equity_end`；如果没有则用 `account.initial_capital`
  - 计算 `equity_end/pnl/return_rate`
  - 生成并 `db.add()`，由上层 commit

- `run_checkpoint_job(interval_seconds=3600)`
  - 扫描所有 AI 账户（`Account.account_type == "AI"` 且 `is_active == "true"`）
  - 对每个账户尝试创建 checkpoint
  - 有新增则 `commit()`，否则 `rollback()`

### 3.3 定时任务接入（APScheduler）

文件：`backend/services/startup.py`

在 `initialize_services()` 里增加：

- 环境变量：
  - `EVAL_CHECKPOINT_INTERVAL_SECONDS`：结算周期长度（秒）。支持逗号分隔多个值，例如 `900,3600,86400`。
    - 当前默认：`900,3600,86400`（15m/1h/1d），与前端下拉保持一致
  - `EVAL_CHECKPOINT_POLL_SECONDS`：轮询频率（默认 30）

- 启动任务：
  - 通过 `task_scheduler.add_interval_task(..., task_id="eval_checkpoint_job")`
  - 使用 wrapper `_run_eval_checkpoint_job()` 避免参数名冲突

### 3.4 API 接口（给前端查询/展示）

文件：`backend/api/evaluation_routes.py`

注册位置：`backend/main.py`（`app.include_router(evaluation_router)`）

提供接口：

1) 最新周期排行榜

- `GET /api/evaluation/checkpoints/leaderboard`
- Query:
  - `interval_seconds`（默认 3600）
  - `period_end`（可选；不传则取该 interval 的最新 period_end）
  - `order_by`：`return` 或 `pnl`（默认 `return`，当前前端使用 `pnl`）
  - `limit`（默认 50）

返回：该周期各 account 的 `pnl/return_rate` 等信息。

2) 单个 agent 的 checkpoint 列表

- `GET /api/evaluation/checkpoints/account/{account_id}`
- Query:
  - `interval_seconds`（默认 3600）
  - `limit`（默认 200，上限 2000）

返回：该 account 最近 N 条 checkpoint（按 period_end 倒序）。

3) 区间拉取（用于对比/画图）

- `GET /api/evaluation/checkpoints/compare`
- Query:
  - `interval_seconds`
  - `start` / `end`（过滤 period_end）
  - `limit`

---

## 4. 前端实现

### 4.1 前端 API 封装

文件：`frontend/app/lib/api.ts`

新增：

- `getEvalLeaderboard(intervalSeconds, orderBy)`
  - 调用：`/evaluation/checkpoints/leaderboard?interval_seconds=...&order_by=...`

- `getEvalAccountCheckpoints(accountId, intervalSeconds, limit)`
  - 调用：`/evaluation/checkpoints/account/{accountId}?interval_seconds=...&limit=...`

### 4.2 UI 展示（LiveMACE bench右侧卡片）

文件：`frontend/app/components/portfolio/AccountDataView.tsx`

在右侧面板（AccountSelector 下方）新增卡片“Agent 结算评分”：

1) 周期选择（默认 1h）

- 下拉可选：`15m / 1h / 1d`
- 切换后重新拉取 leaderboard 与 checkpoints

2) 排行榜（最新周期）

- 表格列：排名、Agent 名称、PnL、Return
- 交互：**点击某一行**，自动将下方 checkpoints 选中 agent 切到该行对应 account
- 排序方式不联动改变（保持固定按 PnL 拉取）

3) Checkpoints 列表（按周期）

- agent 下拉：可从 accounts 列表或 leaderboard 生成候选
- 条数可选：`5 / 10 / 20`，默认 10
- 展示列：周期截止时间、PnL、Return

> 说明：当前实现以“卡片区块”形式插入，不新增页面/路由。

---

## 5. 如何运行与验证

### 5.1 启动

在仓库根目录：

```bash
pnpm dev
```

- 后端默认：`http://localhost:5611`
- 前端默认：`http://localhost:5621`

### 5.2 验证接口

- Leaderboard：

```text
GET http://localhost:5611/api/evaluation/checkpoints/leaderboard?interval_seconds=3600&order_by=pnl
```

- Checkpoints：

```text
GET http://localhost:5611/api/evaluation/checkpoints/account/1?interval_seconds=3600&limit=10
```

### 5.3 验证前端

打开前端：`http://localhost:5621/`

- 在“LiveMACE bench”右侧看到“Agent 结算评分”卡片
- 周期可切换；点击排行榜行可切换下方 agent；checkpoint 条数可选 5/10/20

---

## 6. 已知限制与后续扩展建议

- 如果想做更严格的“控制变量”，可以考虑在每个周期做资金/仓位重置（但这会改变资金滚动的设定）。
- 如果要进一步提升公平性与可解释性，可以扩展指标：最大回撤、波动率、胜率、收益回撤比等。
- 当前 checkpoint 的 `equity_start` 取自上一条 checkpoint 的 `equity_end`（链式滚动），更符合资金滚动设定。

