---
rfc: "0008"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0007；frontend/app；frontend/vite.config.ts；frontend/nginx.conf"
---

# RFC-0008: 前端控制台重构

---

## Summary

本 RFC 规划 Vite/React 前端重构。目标是保持前端作为交易系统控制台和观察面，不承担交易事实源；同时整理 API client、WebSocket state、账户设置、交易面板、组合视图、合规视图、记忆视图和评测榜单的状态边界。

## Motivation

前端已经按领域分组：

- `components/portfolio`
- `components/trading`
- `components/compliance`
- `components/memory`
- `components/layout`
- `components/agent`
- `lib/api.ts`
- `lib/compliance-api.ts`

主要问题是 API 类型手写、WebSocket 与 REST 数据合并边界不清、账户 flags 类型不稳定、错误展示不统一。

## Goals

- 建立 `api client -> domain hooks -> components` 三层。
- 所有交易写操作只通过 REST API，不通过 WebSocket。
- WebSocket 快照只更新展示状态，不直接推导交易结果。
- 前端统一处理 loading/error/stale 状态。
- 保持 Vite proxy：开发和生产都使用 `/api`、`/ws`，不硬编码后端端口。

## Non-Goals

- 不重做 UI 视觉体系。
- 不引入大型状态管理库，除非现有 hooks 无法支撑。
- 不把前端改为 SSR。
- 不在前端实现交易校验的最终判定。

## Detailed Design

### 3.1 目录结构

建议逐步整理：

```text
frontend/app/lib/api/
  client.ts
  generated-types.ts
  accounts.ts
  trading.ts
  evaluation.ts
  compliance.ts

frontend/app/hooks/
  useAccounts.ts
  usePortfolioSnapshot.ts
  useTradingActions.ts
  useCompliance.ts
```

组件只消费 hook，不直接拼接 API endpoint。

### 3.2 API Client

`apiRequest()` 需要：

- 支持新旧错误响应。
- 支持 request id 或 trace id 透传。
- 对非 JSON 响应给出明确错误。
- 保留 `/api` 相对路径，不硬编码端口。

### 3.3 WebSocket State

前端维护：

- `connectionStatus`: `connecting|open|closed|reconnecting`
- `lastSnapshotAt`
- `snapshotVersion`
- `stale` 标记

REST 写操作成功后不直接假定状态已变更，而是等待下一次 snapshot 或主动 refetch。

### 3.4 账户设置

账户 flags 在 UI 中统一为 boolean：

- `memory_enabled`
- `tool_routing_enabled`
- `enable_rule_aware`
- `is_active`

提交时由 API client 兼容后端当前 string/bool 接口；后端稳定后再移除 string 兼容。

### 3.5 交易面板

交易面板需要区分：

- 用户输入校验：前端即时提示。
- 后端拒绝：展示后端 `error.code` 与 message。
- 市场状态：US closed、Crypto available、price stale。
- 下单结果：order、trade、position 更新来自后端响应或 WS snapshot。

### 3.6 组合和评测视图

- 资产曲线组件消费 full snapshot 或 evaluation endpoint，不自行重算复杂资产。
- leaderboard/order_by/interval 状态反映在 URL query 或本地状态中。
- 空数据、stale 数据、加载失败都应有明确 UI 状态。

## Testing

- TypeScript build 通过。
- API client 能解析 `detail: string`、`detail.error.message`、`message` 三种错误。
- WebSocket 断开后 UI 标记 stale，不展示为最新数据。
- 账户 flag 从后端 string/boolean 输入时 UI 都显示正确。
- 交易表单不会在前端构造 unsupported symbol/market 组合。

## Cross-Impact

- RFC-0007 提供稳定 schema 与 WS message。
- RFC-0005 提供 stale/unavailable 行情状态。
- RFC-0009 的合规与评测 API 需要前端 hook 消费。

