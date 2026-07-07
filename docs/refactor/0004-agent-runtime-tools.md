---
rfc: "0004"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0003；backend/services/agent；docs/agent_architecture.md；docs/GEMINI_MESSAGE_FLOW.md"
---

# RFC-0004: Agent 运行时与工具系统重构

---

## Summary

本 RFC 规划 `backend/services/agent/` 的运行时重构，目标是把 ReAct 循环、LLM Client、工具注册、动态工具路由、交易工具、记忆、搜索子智能体和 Docker 沙箱工具整理为可测试的 Agent Runtime。

重点不是改变 Agent 行为，而是让工具调用协议、终止条件、trace 记录和交易执行状态稳定下来。

## Motivation

当前 Agent runtime 包含：

- `react.py`：ReAct loop、工具调用 guardrail、`<TRADE_DONE>` 终止。
- `env_wrapper.py`：按账户/session 注册工具。
- `tools.py`：工具注册契约。
- `tool_selector.py`：动态工具路由 meta-tool。
- `trade_execution_tool.py`：模型可调用的即时交易工具。
- `llm_client.py`：OpenAI-compatible client 与 provider normalization。
- `memory_*.py`、`memory_tools.py`：账户级记忆。
- `sub_agents/search_agent.py`：搜索子智能体。

这些能力彼此耦合，但缺少统一的 tool result 和 trace envelope，导致交易执行状态、工具失败和模型终止难以稳定断言。

## Goals

- 定义统一 `ToolCallResult` 与 `AgentRunResult`。
- 让 `execute_trade` 的结果显式标记 `executed_trades` 与 `execution_state`。
- 把 LLM provider normalization 限定在 `llm_client.py`，不外溢到业务服务。
- 让工具 schema、prompt、实际 Python 函数和 round-trip 测试保持一致。
- 让 `<TRADE_DONE>`、最大步数、提醒阈值和 fallback HOLD 的行为可测试。
- 保持账户级开关字符串/布尔兼容。

## Non-Goals

- 不重写 prompt 目标策略。
- 不替换 OpenAI-compatible API。
- 不移除 legacy JSON 决策路径。
- 不把所有工具改造成外部插件。

## Detailed Design

### 3.1 AgentRunResult

建议内部结果结构：

```python
AgentRunResult = {
    "protocol": "tool" | "advisory" | "failed" | "hold_fallback",
    "decision": dict,
    "executed_trades": list[dict],
    "trace_id": str,
    "decision_round_id": str | None,
    "termination_reason": "trade_done" | "max_steps" | "llm_error" | "tool_error" | "fallback",
}
```

`call_agent_for_decision()` 对外仍可返回 dict，但必须包含足够字段让 RFC-0003 判定是否已执行。

### 3.2 Tool Registry

工具注册分三类：

| 类型 | 示例 | 约束 |
| --- | --- | --- |
| 状态查询 | market snapshot、account state、kline history | 只读，可缓存 |
| 外部信息 | search sub-agent、public API tools | 有超时、限流和审计 |
| 状态修改 | execute_trade、memory write、sandbox write | 必须有 guardrail 与 trace |

每个工具声明：

- `name`
- `schema`
- `side_effect_level`: `read_only|external_read|sandbox_write|trading_write|memory_write`
- `timeout_seconds`
- `cache_policy`
- `trace_redaction`

### 3.3 交易工具契约

`execute_trade_tool()` 必须返回：

- 是否执行：`executed: true|false`
- 拒绝原因：`reject_code`、`reject_message`
- 订单/成交引用：`order_id`、`trade_id`
- 现金/持仓摘要
- 原始模型参数与归一化参数

Agent loop 收到成功执行后引导模型输出 `<TRADE_DONE>`；如果模型没有输出，runtime 仍以 tool result 为事实，不允许上层重复执行。

### 3.4 Trace 与审计

所有 LLM message、tool call、tool output、termination reason 写入 `AgentTrace`。字段过长依赖 MySQL `LONGTEXT` 迁移，SQLite 保持本地可用。

敏感字段：

- API key、base_url credential、search provider key 必须 redacted。
- sandbox 文件内容按大小限制记录摘要。
- 交易参数必须完整保留，便于审计。

### 3.5 记忆后端

当前存在 Pinecone/Chroma/memory tools。重构目标：

- 定义 `MemoryStore` 协议：`search`、`write`、`delete`、`healthcheck`。
- 账户级 `memory_enabled` 为 false 时不注册 memory tools。
- Pinecone credential 缺失时不要在 Agent run 中 late crash；启动或账户配置检查应提前暴露。

### 3.6 LLM Client

`llm_client.py` 负责：

- OpenAI-compatible 参数拼装。
- provider-specific tool-call normalization。
- request retry 只在该层或统一 retry helper 中发生，避免 route/service 自建重试。
- 将模型原始错误转成结构化 `LLMError`。

## Testing

必须保留并扩展：

- `test/test_tool_call_guardrails.py`
- `test/test_tool_use_dynamic_schema.py`
- `test/test_llm_tool_signature_roundtrip.py`
- `test/test_agent_tool_edge_cases.py`
- `test/test_tool_selector_execute_trade.py`

新增测试：

- `execute_trade` 成功后 `AgentRunResult.protocol == "tool"` 且 `executed_trades` 非空。
- `<TRADE_DONE>` 缺失但工具已执行时，上层仍不会重复执行。
- memory disabled 时 schema 中没有 memory tools。
- provider tool-call normalization 对 Gemini/OpenAI-compatible 输出一致。

## Cross-Impact

- RFC-0003 依赖 `AgentRunResult` 判断执行状态。
- RFC-0006 依赖 trace 字段容量与迁移。
- RFC-0009 依赖 trace 与 tool result 做合规审计。

