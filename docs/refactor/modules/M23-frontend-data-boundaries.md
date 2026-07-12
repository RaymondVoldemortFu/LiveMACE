# M23：前端 API、WebSocket 与 Domain Hooks 分层

## 交付目标

整理现有前端的数据访问结构，消除组件直接 fetch 和重复 DTO；保持页面、交互、样式和业务功能不变，并容纳 M15 扩展设置。

## 文件边界

- 主改：`frontend/app/lib/api.ts`、`lib/compliance-api.ts`、`main.tsx`、所有直接 fetch 的组件。
- 新增：`frontend/app/lib/api/`、`frontend/app/lib/ws/`、`frontend/app/hooks/`。
- 不改视觉组件样式、图表算法和后端。

## 目标模块

```text
lib/api/client.ts
lib/api/generated-types.ts
lib/api/accounts.ts
lib/api/trading.ts
lib/api/market.ts
lib/api/agent.ts
lib/api/evaluation.ts
lib/api/compliance.ts
lib/api/extensions.ts
lib/ws/messages.ts
lib/ws/client.ts
hooks/useAccounts.ts
hooks/usePortfolioSnapshot.ts
hooks/useTradingActions.ts
hooks/useAgentTrace.ts
```

## TODO

- [ ] 从 M21 OpenAPI 生成或校验 TypeScript types；禁止组件自定义同名 Account/Order/Position DTO。
- [ ] `apiRequest` 统一 JSON、request id、新旧错误解析和取消；各 domain client 只拼 endpoint。
- [ ] 删除不可达 `/api/users`、`/api/accounts` 封装，除非 M21 明确注册并由 UI 使用。
- [ ] 将 `TradingPanel`、`StockViewer`、`AccountDataView` 等直接 fetch 迁到 domain client/hook。
- [ ] WS message 建 discriminated union；client 负责 singleton、重连、heartbeat、订阅和 decode。
- [ ] App state 可保留在顶层，但 snapshot merge 迁到 `usePortfolioSnapshot`；fast 不清空 full curve。
- [ ] REST 写操作完成后等待/refetch snapshot，不在前端计算资金/持仓结果。
- [ ] 合并 M15 extension API re-export，删除旧单文件 API facade。

## 验收

- `rg 'fetch\(' frontend/app/components frontend/app/main.tsx` 无命中。
- `main.tsx` 不再定义后端 DTO 或实现 WS 协议解析。
- 页面、账户切换、交易、曲线、Agent trace、memory、compliance 功能与 M00/e2e fixture 一致。
- `pnpm run build:frontend` 通过，OpenAPI 类型 drift 检查通过。

## 前置与并行

前置 M21。API domain clients 可并行；`main.tsx`/WS client 由单一 owner 集成。M15 先新增 extension 模块，最后在此任务合并总入口。

