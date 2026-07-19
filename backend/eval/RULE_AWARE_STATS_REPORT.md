# Rule-Aware 离线统计脚本说明

本说明对应脚本：`eval/rule_aware_stats_report.py`

## 目标

在不启动后端服务的情况下，直接读取 SQLite 数据库（例如 `backend/alpha_arena.sqlite`），生成 Rule-Aware Agent 的统计结果与趋势图。

脚本对齐前端合规看板的核心统计口径：

- 账户总评估数（`total_evaluations`）
- 通过率（`gate_pass_rate`）
- 平均分（`avg_s_rule_sat` / `avg_s_audit` / `avg_final_score`）
- 最近 7 天统计（`recent_7d`）
- LLM 审计统计（`llm_audit_score / coverage / conflict`）
- 三条趋势图：`s_rule_sat`、`s_audit`、`final_score`

## 运行方式

按你的要求，使用 conda 的 `uvbench` 环境：

```bash
cd backend
conda run -n uvbench python eval/rule_aware_stats_report.py --db-path ./alpha_arena.sqlite
```

## 常用参数

```bash
--db-path PATH
```

- SQLite 文件路径，默认 `./alpha_arena.sqlite`

```bash
--output-dir PATH
```

- 输出根目录，默认 `./eval_outputs/rule_aware_stats`
- 每次运行会自动创建时间戳子目录，例如：
   `./eval_outputs/rule_aware_stats/run_20260407_214530/`

```bash
--days N
```

- 趋势图使用最近 N 天数据，默认 30

```bash
--account-id ID
```

- 仅统计指定账户，可重复传多次

```bash
--no-charts
```

- 仅导出统计，不生成 PNG 图

## 输出内容

默认输出目录（可通过 `--output-dir` 修改）下会生成：

- `run_YYYYMMDD_HHMMSS/`（每次运行一个子目录）

- `account_summary.csv`：账户维度统计汇总
- `account_summary.json`：同上，JSON 格式
- `trend_s_rule_sat.png`：规则满足度趋势图
- `trend_s_audit.png`：LLM 审计分趋势图
- `trend_final_score.png`：最终分趋势图

## 口径说明

1. Rule-Aware 账户筛选：
   - 优先使用 `accounts.enable_rule_aware == 'true'`
   - 若不存在该列，则回退到 `accounts.agent_type == 'rule_aware'`

2. 时间序列处理：
   - 与前端看板一致，按 5 分钟粒度归一化
   - 同一账户同一 5 分钟窗口内多条记录取平均

3. 分数字段来源：
   - 来自 `rule_evaluation_results` 表
   - 包含：`s_rule_sat`、`s_audit`、`final_score`

## 示例

只看最近 14 天并输出到自定义目录：

```bash
cd backend
conda run -n uvbench python eval/rule_aware_stats_report.py \
  --db-path ./alpha_arena.sqlite \
  --days 14 \
  --output-dir ./eval_outputs/rule_aware_14d
```

仅导出账号 2 和 3 的统计，不画图：

```bash
cd backend
conda run -n uvbench python eval/rule_aware_stats_report.py \
  --db-path ./alpha_arena.sqlite \
  --account-id 2 --account-id 3 \
  --no-charts
```
