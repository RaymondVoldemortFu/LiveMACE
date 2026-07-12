# M14：扩展 Catalog 与账户配置 API

## 交付目标

提供用户无需读源码即可查看可用 Agent/Tool/Prompt、读取 schema、验证并保存账户运行配置的 HTTP API。不允许通过 API 上传或执行扩展代码。

## 文件边界

- 新增：`backend/api/extension_routes.py`、`backend/schemas/extensions.py`、`backend/services/extension_config_service.py`。
- 修改：`backend/main.py` 仅注册 router（若 M18 已引入 app factory，则改 router registry）。
- 不修改 frontend。

## HTTP 契约

```text
GET  /api/extensions
GET  /api/extensions/agents
GET  /api/extensions/toolsets
GET  /api/extensions/prompts
GET  /api/extensions/components/{component_id}/schema
GET  /api/account/{account_id}/runtime-config
POST /api/account/{account_id}/runtime-config/validate
PUT  /api/account/{account_id}/runtime-config
```

列表响应必须包含 id、name、version、description、source=`builtin|external`、status、config schema、requested/allowed capabilities；不得包含 entrypoint、绝对路径或密钥。

## TODO

- [ ] 为所有 request/response 建 Pydantic schema 和 OpenAPI examples。
- [ ] validate 为纯校验不写库；PUT 先校验再以乐观锁保存。
- [ ] 未知 component、版本不可用、schema 错误、capability 不足使用统一错误码。
- [ ] account_id 不存在返回 404；配置冲突返回 409；非法 config 返回 422。
- [ ] extension catalog 为只读；不提供运行中 install/reload/unload API。
- [ ] system prompt preview 保留原功能，并增加 prompt id/version/hash 与 rendered content。
- [ ] 增加 route contract test，验证 secret/path 不在响应。

## 验收

- OpenAPI 中完整出现上述端点和 schema。
- 仅通过 API 可完成“列出 -> 验证 -> 保存 -> 读取”闭环。
- 保存 invalid config 不修改数据库原配置。
- API route 不直接 query ORM 或操作 registry，全部通过 service/catalog。

## 前置与并行

前置 M12、M13。可与 M16/M18/M20 实施；M15 依赖此契约。

