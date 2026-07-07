---
rfc: "0006"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0002；backend/database；backend/repositories；backend/main.py"
---

# RFC-0006: 数据库、迁移与仓储边界重构

---

## Summary

本 RFC 规划数据库层重构。目标是把 SQLAlchemy model、engine/session、启动迁移、repository helper 和业务 service 的职责分开，并显式处理 SQLite 本地 fallback 与 MySQL 生产路径的差异。

## Motivation

当前仓库没有 Alembic，schema 变更依赖：

- `Base.metadata.create_all()`
- `backend/main.py` 中的 SQLite `ALTER TABLE`
- `backend/main.py` 中的 MySQL 字段加宽
- maintenance scripts

这能支撑迭代，但随着 Agent trace、evaluation checkpoint、account flags、encrypted api_key 等字段增加，启动迁移逻辑会继续膨胀。

## Goals

- 把启动迁移从 `main.py` 移到 `database/migrations_startup.py` 或 `services/bootstrap/schema.py`。
- 为每个启动迁移建立 idempotent 检查和日志。
- repository 只负责 DB 访问，不承载交易决策。
- 明确跨线程 session 使用规范。
- 为后续引入 Alembic 预留版本表或迁移登记点。

## Non-Goals

- 本阶段不强制引入 Alembic。
- 不改变默认 SQLite fallback。
- 不迁移历史 SQL dump。
- 不重命名所有 model 字段。

## Detailed Design

### 3.1 Schema Migration Registry

把当前 ad hoc 迁移登记为：

| migration id | dialect | 当前动作 |
| --- | --- | --- |
| `202606_agent_checkpoint_volatility` | sqlite | `agent_period_checkpoints.volatility` |
| `202606_account_tool_routing_enabled` | sqlite | `accounts.tool_routing_enabled` |
| `202606_ai_decision_reason_text` | mysql | `ai_decision_logs.reason TEXT` |
| `202606_agent_traces_longtext` | mysql | `agent_traces.content/tool_calls/tool_output LONGTEXT` |

每个 migration 需要：

- 检查函数。
- apply 函数。
- 成功、已存在、失败日志。
- 是否 fatal。

### 3.2 Repository 边界

允许 repository 做：

- 按 id/name/status 查询。
- 简单 create/update/delete。
- 带分页/过滤的列表查询。
- 必要的 row lock 或事务 helper。

不允许 repository 做：

- 调 LLM。
- 调外部行情。
- 执行交易策略。
- 发送 WebSocket。

### 3.3 Session 纪律

- FastAPI request 使用 request-scoped session。
- scheduler job 显式创建并关闭 session。
- decision worker thread 每个 account 一个 `SessionLocal()`，不得跨线程复用。
- service 不持有全局 session。
- 测试 fixture 可以使用 transaction rollback，但不能导入 `main:app` 后误启后台任务。

### 3.4 字段兼容

必须保留这些兼容性：

- 多个 bool-like 字段在 DB 中以 `"true"` / `"false"` 字符串存在。
- `api_key` 可能为空、默认占位、legacy plaintext、hashed、encrypted。
- `AgentTrace` 大字段在 MySQL 中需要 `LONGTEXT`。
- SQLite 并发语义不能代表 MySQL 生产路径。

### 3.5 未来 Alembic 入口

本阶段先新增 `schema_migrations` 轻量表可选登记已执行 startup migration。若后续引入 Alembic：

- startup migration registry 冻结，只保留兼容旧库升级。
- 新 schema 变更走 Alembic revision。
- Docker startup 先迁移，再启动 runtime services。

## Testing

- 空 SQLite 启动建表和 seed 成功。
- 旧 SQLite 缺列时 idempotent 添加列，重复运行不报错。
- MySQL 字段已是 LONGTEXT 时不报 fatal。
- repository 测试不需要启动 scheduler。
- legacy plaintext api_key 迁移后不再明文返回。

## Cross-Impact

- RFC-0002 负责调用 migration registry。
- RFC-0004 依赖 trace 字段容量。
- RFC-0007 的 serializer 需要处理 bool-like 字段。
- RFC-0010 需要在测试矩阵中区分 SQLite 与 MySQL。

