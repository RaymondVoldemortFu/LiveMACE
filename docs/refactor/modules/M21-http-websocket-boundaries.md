# M21：HTTP Route 与 WebSocket 会话边界

## 交付目标

将所有 route 收敛为 Pydantic DTO + application service，统一 HTTP/WS 交易入口和错误格式，保持现有 URL、消息和页面功能。删除不可达遗留接口或显式注册，不留下前端调用死链。

## 文件边界

- 修改：`backend/api/*.py`、`backend/schemas/*.py`、app router registration。
- 新增：按域 application query/command services、WS message schemas/session service。
- 不修改 application 核心实现和 frontend。

## HTTP 规范

- 保留当前已注册 `/api/account`、`orders`、`market`、`config`、`ranking`、`crypto`、`agent`、`memory`、`rules`、`compliance`、`evaluation` URL。
- 每个 endpoint 声明 request/response model；不返回 ORM 或临时未声明 dict。
- route 不直接 `db.query`、commit/rollback、调用 provider 或撮合函数。
- 错误统一为公共规范格式，保留旧 status code。

## WS 规范

现有 client message 保持：`bootstrap`、`subscribe`、`switch_user`、`switch_account`、`get_snapshot`、`get_asset_curve`、`place_order`、`ping`。新增 Pydantic discriminated union 只改变内部解析，不改变 wire 功能。

## TODO

- [ ] 生成实际 endpoint 清单并为每个 route 指定 application service；逐文件移除 ORM 查询。
- [ ] `place_order` WS 与 orders HTTP 都调用 M11 gateway/order command。
- [ ] WS connection manager 始终以 account id 注册；修正 user/account key 混用但保持 switch 功能。
- [ ] snapshot query 与 send 解耦；fast/full 频率和 payload 保持当前行为。
- [ ] `user_routes.py`、`account_management_routes.py`：根据前端实际功能做一次明确决定。若认证功能不属于当前运行系统则删除文件及 `api.ts` 死封装；不得仅保留未注册代码。
- [ ] 注册 M14 extension router；health/readiness 调用 bootstrap status service。
- [ ] 生成 OpenAPI snapshot 并做 breaking diff 检查。

## 验收

- `rg 'db\.query|SessionLocal' backend/api` 无命中（标准 `Depends(get_uow)` 除外）。
- M00 HTTP/WS fixture payload 与功能通过；交易数据库副作用一致。
- 前端使用的每个 endpoint 在 OpenAPI 中存在；不存在的封装已删除。
- WS 断开清理 account job/connection，无 user id 误作 account id。

## 前置与并行

前置 M10、M11、M18、M19、M20。可按 route domain 并行，但 `ws.py` 和 router registration 各指定单一 owner。

