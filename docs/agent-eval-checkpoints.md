# Agent 周期结算与排行榜

Checkpoint 按固定时间片记录账户权益、损益、收益率和波动率，用于比较不同账户在同一周期的表现。

## 计算口径

周期结束时间按 UTC 对齐，记录由 `(account_id, interval_seconds, period_end)` 唯一标识。重复执行同一周期不会新增第二条记录。

- `equity_end`：计算时账户现金与持仓权益之和。
- `equity_start`：上一条同周期 checkpoint 的 `equity_end`；首次使用账户初始资金。
- `pnl = equity_end - equity_start`。
- `return_rate = pnl / equity_start`；起始权益非正时返回 `0`。
- `volatility`：包含当前周期在内、最多最近 20 个收益率的总体标准差；不足两个样本时为 `0`。

杠杆持仓权益按入场保证金和方向相关的未实现损益计算。结算时行情与账户数据代表实际计算时点，周期边界本身不提供历史行情回放。

## 配置与调度

```env
EVAL_CHECKPOINT_INTERVAL_SECONDS=900,3600,86400
EVAL_CHECKPOINT_POLL_SECONDS=30
```

默认周期为 15 分钟、1 小时和 1 天。完整服务启动时，bootstrap 的任务注册表安装 checkpoint 定时任务；`NO_BACKGROUND` 模式不启动该任务。

`CheckpointService.run_due()` 编排账户和周期，持久化适配器负责事务与 savepoint。单个账户或周期计算失败时回滚该计算；成功创建的记录由批次提交。

## 读取接口

| 接口 | 用途 | 常用参数 |
| --- | --- | --- |
| `GET /api/evaluation/checkpoints/leaderboard` | 指定周期排行榜 | `interval_seconds`、`period_end`、`order_by`、`limit` |
| `GET /api/evaluation/checkpoints/account/{account_id}` | 账户结算记录 | `interval_seconds`、`limit` |
| `GET /api/evaluation/checkpoints/compare` | 账户周期对比 | 以服务 `/docs` 中的参数定义为准 |

排行榜支持按 `return`、`pnl` 或 `volatility` 排序。响应结构由 `backend/api/evaluation_routes.py` 的 DTO 定义，并生成前端类型。

## 实现入口

| 位置 | 职责 |
| --- | --- |
| `backend/benchmark/application/evaluation/checkpoint.py` | 批次编排与结果 |
| `backend/benchmark/persistence/checkpoints.py` | Session、事务、savepoint 和关闭 |
| `backend/services/evaluation/checkpoint_service.py` | 时间片、权益和指标计算 |
| `backend/benchmark/bootstrap/tasks.py` | 后台任务注册 |
| `backend/api/evaluation_routes.py` | HTTP 参数和响应契约 |
| `frontend/app/lib/api/evaluation.ts` | 前端领域客户端 |
| `frontend/app/components/portfolio/AccountDataView.tsx` | 周期选择、排行榜和账户结算展示 |

其他评估公式见[评估指标说明](evaluation/metrics.md)。
