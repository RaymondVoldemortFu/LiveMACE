---
rfc: "0009"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "backend/services/evaluation；backend/api/compliance_routes.py；docs/llm_audit_config.md；docs/agent-eval-checkpoints.md"
---

# RFC-0009: 评测、合规与观测重构

---

## Summary

本 RFC 规划 evaluation、compliance、LLM audit 和运行观测的重构。目标是把“交易系统是否有效、是否合规、为什么做出决策、哪里失败”纳入统一数据链路，而不是只依赖前端图表或散落日志。

## Motivation

仓库已有：

- periodic evaluation checkpoint job
- leaderboard/checkpoint API
- `backend/services/evaluation/*`
- compliance routes 与 dashboard
- `docs/llm_audit_config.md`
- Agent trace 与 AI decision log

但这些数据源之间缺少统一关联键，难以回答同一轮自动交易中：模型看到什么、用了什么工具、执行了什么交易、触发了哪些规则、资产表现如何。

## Goals

- 使用 `decision_round_id` 贯穿 Agent trace、tool cache、AI decision log、execution result、compliance result。
- 评测 checkpoint 与账户/agent_type/model/config 维度对齐。
- 合规规则输出结构化结果，支持前端统计和审计回放。
- 错误日志、工具失败、provider 失败保留可检索字段。
- 明确哪些 evaluation 测试依赖真实 provider，哪些可本地 deterministic 运行。

## Non-Goals

- 不把 LLM judge 作为交易执行的强制前置。
- 不改变排行榜指标定义，除非另行 RFC。
- 不引入全量日志平台。
- 不保存敏感 API key 或未脱敏请求体。

## Detailed Design

### 3.1 统一关联键

每次自动交易 round 生成：

```text
decision_round_id = <timestamp>-<uuid-short>
```

贯穿：

- tool cache key prefix
- AgentTrace
- AIDecisionLog
- order/trade metadata（如当前 schema 不支持，先通过 log 关联）
- compliance audit result
- evaluation checkpoint input snapshot

### 3.2 Evaluation Checkpoint

checkpoint job 保持幂等：按 `(account, interval, period_end)` 去重。重构重点：

- interval 配置解析集中化。
- checkpoint 计算与 scheduler 注册分离。
- leaderboard API 输出包含 `agent_type`、`model`、`config flags`。
- volatility、return、pnl 的计算输入可追踪。

### 3.3 Compliance Result

合规规则输出：

```json
{
  "rule_id": "max_leverage",
  "severity": "info|warning|error",
  "passed": false,
  "subject": {
    "account_id": 1,
    "decision_round_id": "..."
  },
  "message": "...",
  "evidence": {}
}
```

前端 dashboard 只展示结果，不重新实现规则。

### 3.4 Agent Trace 与 LLM Audit

- LLM 请求、工具调用、工具输出、终止原因进入 trace。
- LLM audit 配置缺失时按文档策略 fallback，但必须记录“未启用”的原因。
- provider 错误记录 provider、model、status code、retryable、attempt。
- 敏感字段 redaction 在写日志前完成。

### 3.5 日志格式

建议统一关键事件：

- `decision.round.started`
- `decision.account.collected`
- `decision.account.executed`
- `decision.account.rejected`
- `agent.tool.called`
- `agent.tool.failed`
- `compliance.rule.evaluated`
- `evaluation.checkpoint.written`

日志至少包含 `account_id`、`decision_round_id`、`agent_type`、`model`、`trace_id`。

## Testing

- checkpoint job 对同一 period 幂等。
- compliance rule 输出结构满足 schema。
- LLM audit credential 缺失时不会导致普通交易路径崩溃。
- Agent tool failure 写 trace 和结构化日志。
- leaderboard 按 return/pnl/volatility 排序稳定。

## Cross-Impact

- RFC-0003 生成 execution result 和 decision round。
- RFC-0004 提供 trace 细节。
- RFC-0007/RFC-0008 暴露和展示 evaluation/compliance schema。
- RFC-0010 将 provider 依赖测试标记为 integration。

