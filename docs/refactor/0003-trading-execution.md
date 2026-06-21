---
rfc: "0003"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0001；RFC-0004；backend/services/trading_commands.py；backend/services/agent/trade_execution_tool.py"
---

# RFC-0003: 交易执行语义重构

---

## Summary

本 RFC 规划交易主路径重构。目标是把 `trading_commands.py` 中的账户加载、行情准备、决策收集、baseline tick、决策分发、订单执行、日志保存拆成清晰阶段，同时守住最关键的不变量：Agent/tool 协议中已经执行的交易不得被 legacy 决策处理器重复执行。

## Motivation

当前主流程大致为：

```text
APScheduler
  -> place_ai_driven_crypto_order()
     -> load active AI + baseline accounts
     -> fetch latest market prices
     -> collect decisions concurrently with isolated DB sessions
     -> ReAct/multi-agent path or legacy JSON path
     -> execute trade immediately or process returned decision
     -> save AIDecisionLog / AgentTrace / Order / Trade / Position
```

风险集中在三个方面：

- legacy JSON 决策和 Agent/tool 决策共存，返回结构相似但执行语义不同。
- 交易执行涉及现金、冻结资金、杠杆、方向、持仓均价、订单和成交，细微改动会放大为资产曲线错误。
- baseline 账户与 AI 账户在同一 loop 中运行，职责容易混合。

## Goals

- 引入显式 `DecisionEnvelope`，区分 `legacy_json`、`agent_tool_executed`、`agent_advisory`、`baseline_tick`。
- 把交易执行入口收敛为少数函数：普通订单、杠杆 crypto、Agent trade tool。
- 为重复执行、价格无效、市场不匹配、方向冲突、杠杆限制建立统一拒绝结果。
- 把并发决策收集与主事务写入分离，每个 worker 只返回 envelope，不持有跨线程 session。
- 提升日志可追溯性：每轮自动交易有 `decision_round_id`，贯穿缓存、trace、决策日志和执行结果。

## Non-Goals

- 不改变支持的交易品种集合。
- 不改变现有 paper trading 撮合语义。
- 不引入真实交易所下单。
- 不重写 baseline 策略逻辑。

## Detailed Design

### 3.1 决策 Envelope

建议新增内部结构：

```python
DecisionEnvelope = {
    "account_id": int,
    "account_name": str,
    "source": "legacy_json" | "agent_tool" | "baseline",
    "execution_state": "not_executed" | "executed_by_tool" | "skipped" | "failed",
    "decision": dict,
    "portfolio_snapshot": dict,
    "decision_round_id": str,
}
```

处理规则：

- `execution_state == "executed_by_tool"`：只保存日志和 trace，不再进入订单执行。
- `source == "legacy_json"`：进入 `_process_account_decision_payload()` 或重构后的 legacy executor。
- baseline 不返回 LLM 决策，不写 `AIDecisionLog` 为 AI 决策。

### 3.2 执行服务拆分

从 `trading_commands.py` 拆出：

- `account_selection.py`：加载 active AI、baseline、按 agent_type 分组。
- `decision_collection.py`：并发收集 Agent/legacy 决策。
- `decision_dispatch.py`：按 envelope 分发。
- `execution_policy.py`：市场、symbol、price、leverage、side、portion 校验。
- `execution_logging.py`：交易日志、AI 决策日志、trace 关联。

### 3.3 拒绝语义

统一拒绝码：

| code | 含义 |
| --- | --- |
| `INVALID_SYMBOL_MARKET` | symbol 与 market 不匹配 |
| `PRICE_UNAVAILABLE` | 价格缺失、异常或非正数 |
| `MARKET_CLOSED` | US 市场关闭 |
| `DUPLICATE_TOOL_EXECUTION_GUARD` | 工具模式交易已执行，拒绝二次执行 |
| `INSUFFICIENT_CASH` | 现金不足 |
| `POSITION_CONFLICT` | 同 symbol 已有相反方向或不同杠杆仓位 |
| `INVALID_LEVERAGE` | crypto short 杠杆不合法或 US 杠杆非法 |
| `INVALID_SIZING` | portion/usd/all_in/close ratio 不合法 |

拒绝结果必须写入决策日志，但不得生成订单和成交。

### 3.4 事务边界

- 决策收集 worker 使用独立 `SessionLocal()`，只读账户与 portfolio，finally 关闭。
- 主线程收到 envelope 后重新加载账户和持仓，执行写事务。
- 执行函数返回结构化 `ExecutionResult`，调用方负责日志保存。
- 对同一轮自动交易保留 `_ai_trade_run_lock`，避免重叠 job。

### 3.5 Baseline 隔离

`BuyHoldBaseline` 与 `GridBaseline` 保留现有策略，但调度入口与 AI 决策入口分离：

```text
run_baseline_tick(accounts, prices)
run_ai_decision_round(accounts, prices, decision_round_id)
```

这样可以单独测试 baseline tick，不启动 LLM。

## Testing

必须保留并扩展：

- `test/trade_execution_semantics_test.py`
- `test/test_tool_selector_execute_trade.py`
- `test/test_trading_loop_lock_isolation.py`

新增测试：

- 工具模式返回 `protocol: "tool"` 后不会二次创建订单。
- `executed_trades` 非空时不会二次执行。
- 无效 price、错误 market、US 闭市、crypto short leverage=1 均拒绝且写日志。
- worker session 全部关闭。
- baseline tick 与 AI decision round 可分别运行。

## Cross-Impact

- RFC-0004 需要让 Agent runtime 明确标记 tool execution state。
- RFC-0005 提供价格不可用的结构化错误。
- RFC-0009 使用 `decision_round_id` 聚合评测和审计。

