---
rfc: "0000"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "AGENT.md；docs/agent_architecture.md；docs/GEMINI_MESSAGE_FLOW.md；backend/services/evaluation/EVALUATION_REPORT.md"
---

# RFC-0000: LiveMACE bench 重构规划索引

---

## Summary

本目录把 LiveMACE bench 的重构拆成一组可独立评审、可分阶段落地的 RFC。拆分依据来自仓库根目录 `AGENT.md` 中定义的运行形态：

```text
Vite/React frontend
  -> FastAPI backend
  -> scheduler + market data + LLM trading agents
  -> order simulator + DB/Redis/Docker sandbox
```

核心原则是：后端继续作为交易、账户、持仓、订单、AI 决策日志、Agent Trace、评测检查点、行情缓存、WebSocket 快照和定时自动交易循环的事实源；前端只作为 `/api` 与 `/ws` 上的控制台，不执行交易逻辑。

## RFC 拆分

| 部分 | 文档 | 主要覆盖范围 |
| --- | --- | --- |
| 总体架构与边界 | [RFC-0001](0001-architecture-boundaries.md) | 分层、模块边界、重构顺序、共享约束 |
| 启动与调度 | [RFC-0002](0002-startup-scheduler.md) | `main.py` 启动副作用、`startup.py`、APScheduler、后台任务生命周期 |
| 交易执行语义 | [RFC-0003](0003-trading-execution.md) | 决策收集、工具模式交易、订单/成交/持仓、重复执行防线 |
| Agent 运行时与工具 | [RFC-0004](0004-agent-runtime-tools.md) | ReAct、多智能体、工具注册、LLM Client、记忆、沙箱与搜索工具 |
| 行情、缓存与外部数据 | [RFC-0005](0005-market-data-cache.md) | Crypto/US 行情、Kline、价格缓存、Redis tool cache、外部 API 降级 |
| 数据库、迁移与仓储 | [RFC-0006](0006-database-repositories.md) | SQLAlchemy 模型、SQLite/MySQL 差异、启动迁移、repository 边界 |
| API 与 WebSocket 契约 | [RFC-0007](0007-api-websocket-contracts.md) | FastAPI 路由瘦身、响应类型、WS 快照、前后端契约 |
| 前端控制台 | [RFC-0008](0008-frontend-control-plane.md) | Vite/React、API client、账户/交易/组合/合规/记忆视图 |
| 评测、合规与观测 | [RFC-0009](0009-evaluation-compliance-observability.md) | evaluation checkpoint、LLM judge、合规规则、日志与审计 |
| 开发体验、部署与测试 | [RFC-0010](0010-devops-testing-release.md) | pnpm/uv、Docker Compose、测试矩阵、配置与发布纪律 |

## 落地顺序

建议按风险从低到高推进：

1. **契约先行**：RFC-0007、RFC-0008 先补类型与边界测试，不改变交易语义。
2. **基础设施收敛**：RFC-0002、RFC-0005、RFC-0006 把启动、缓存、迁移和外部数据的副作用显式化。
3. **核心交易重构**：RFC-0003、RFC-0004 在测试护栏齐备后再切分交易执行和 Agent 工具路径。
4. **评测闭环**：RFC-0009 把效果、合规与审计指标纳入回归。
5. **发布收口**：RFC-0010 固化命令、环境变量、部署与 CI 检查。

## 全局约束

- 不改变“后端是事实源，前端是控制台”的边界。
- 不合并 Legacy JSON 决策与 Agent/tool 决策路径；工具模式已执行交易时，`trading_commands` 不得二次执行。
- 不在 WebSocket 发送路径或 scheduler job 中引入无界阻塞。
- 不绕过 repository/service 直接修改账户、持仓、订单、成交等金融状态，除非局部模块已明确拥有该不变量。
- 对 `current_cash`、`frozen_cash`、数量、均价、保证金等金融数据，沿用周边代码的 `Decimal` 纪律，避免 float-only 重写。
- 保留账户级开关兼容性：`agent_type`、`memory_enabled`、`tool_routing_enabled`、`enable_rule_aware`、`is_active`。
- Redis tool cache 继续作为后端启动必需项；不可把启动失败静默降级为“无缓存运行”。
- SQLite 只作为本地 fallback；MySQL 作为生产/并发路径必须显式验证。

