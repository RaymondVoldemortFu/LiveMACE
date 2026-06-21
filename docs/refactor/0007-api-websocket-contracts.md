---
rfc: "0007"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0008；backend/api；frontend/app/lib/api.ts；backend/api/ws.py"
---

# RFC-0007: API 与 WebSocket 契约重构

---

## Summary

本 RFC 规划 FastAPI 路由和 WebSocket 契约重构。目标是让后端 route 保持薄层，响应 schema 稳定，前端类型与后端 serializer 对齐，WebSocket 快照明确区分 fast snapshot 与 full snapshot。

## Motivation

当前前端 `frontend/app/lib/api.ts` 手写了大量接口类型；后端 route 分散在 `backend/api/*.py`。许多字段存在 DB 字符串、后端 bool、前端 bool 混用，尤其是 account flags。WebSocket 还承担账户级快照推送，需要避免昂贵资产曲线计算拖慢发送路径。

## Goals

- 每个 route 有 Pydantic request/response schema。
- Route 只做参数校验、调用 service、返回 serializer。
- 前端 API client 类型从后端 schema 生成，或至少由 shared contract 文档锁定。
- WebSocket message 定义 `type`、`account_id`、`version`、`payload`。
- 快照计算与发送解耦，避免长阻塞。

## Non-Goals

- 不引入 GraphQL。
- 不改变 `/api` 与 `/ws` 基础路径。
- 不实现多用户认证体系重构。
- 不把 WebSocket 用作写操作入口。

## Detailed Design

### 3.1 Route 分层

```text
api/account_routes.py
  -> schemas/account.py
  -> services/account_service.py
  -> repositories/account_repo.py
```

路由层禁止：

- 直接执行订单撮合。
- 直接操作多张表形成业务事务。
- 直接读取 `.env` 决定业务行为。

### 3.2 Schema 规范

对外响应字段采用稳定类型：

- `is_active`: boolean
- `memory_enabled`: boolean
- `tool_routing_enabled`: boolean
- `enable_rule_aware`: boolean

serializer 内部负责把 DB 中的 `"true"` / `"false"` 转换为 bool。更新接口仍可暂时接受 string/bool 两种输入，以保持兼容。

### 3.3 错误响应

统一错误格式：

```json
{
  "error": {
    "code": "INVALID_SYMBOL_MARKET",
    "message": "...",
    "details": {}
  }
}
```

FastAPI `HTTPException.detail` 可以承载上述结构。前端 `apiRequest()` 需要优先读取 `detail.error.message`，其次兼容旧 `detail` 字符串。

### 3.4 WebSocket Message

```json
{
  "type": "snapshot_fast",
  "version": 1,
  "account_id": 1,
  "server_time": "2026-06-19T00:00:00Z",
  "payload": {}
}
```

类型：

- `snapshot_fast`：账户、现金、持仓、最新价格、近期订单。
- `snapshot_full`：包含资产曲线等昂贵数据，低频发送。
- `error`：连接或账户级错误。
- `heartbeat`：可选心跳。

### 3.5 OpenAPI 与前端类型

短期：

- 在 `docs/api_contract.md` 记录核心 schema。
- 前端类型加注释指向后端 schema。

中期：

- 使用 `openapi-typescript` 从 FastAPI OpenAPI 生成类型。
- CI 检查生成结果是否更新。

## Testing

- Route 测试验证 response schema，不只验证 status code。
- account flag string/bool 输入均可更新，输出统一 bool。
- WebSocket 连接一个 account 只注册一个 snapshot job，断开后清理。
- full snapshot 周期低于 fast snapshot，不在每次发送中重算全量资产曲线。
- 前端 `apiRequest()` 能解析新旧错误格式。

## Cross-Impact

- RFC-0008 消费稳定 API/WS 契约。
- RFC-0003 的拒绝码通过 API 错误格式暴露。
- RFC-0009 的合规和评测路由需要 schema 化。

