# M12：账户扩展配置模型与迁移

## 交付目标

让账户显式保存 Agent、Toolset、Prompt profile 及版本化配置，替代只有 `agent_type` 和若干 flags 的隐式组合；迁移后原账户行为不变。

## 文件边界

- 修改：`backend/database/models.py`、`schemas/account.py`、`repositories/account_repo.py`、启动 migration/seed 模块。
- 新增：账户 extension config serializer、validation service 和迁移测试。
- 不修改 Agent runtime 和 UI。

## 数据模型

新增独立 `AccountRuntimeConfig` 表，避免继续扩张 `accounts`：

```text
account_id unique FK
agent_id
agent_version
agent_config_json
toolset_ids_json
disabled_tools_json
prompt_profile_id
prompt_profile_version
component_versions_json
validation_status
validation_errors_json
updated_at
```

## TODO

- [ ] 建 model、repository 和版本化 migration；SQLite/MySQL 均验证。
- [ ] 迁移映射：现有 `agent_type` -> `core.*`；memory/tool routing/rule aware 转为相同默认 toolset/profile/config。
- [ ] 迁移是幂等的；旧列在本阶段保留只读，所有运行时读取切到新表后再单独删除。
- [ ] 提供 `get_runtime_config(account_id)` 和 `save_runtime_config(account_id, config, expected_updated_at)` 乐观并发接口。
- [ ] 保存前调用 Extension Catalog 验证组件、schema、capability 和 Prompt profile。
- [ ] 引用不存在/卸载扩展时标记 invalid，不自动替换。
- [ ] API key/model/base_url 暂留 Account，不混入公开扩展 config。

## 验收

- 现有测试数据库迁移后所有账户解析到等价内置 Agent、工具和 Prompt。
- 重复 migration 无重复行；并发更新有明确 conflict。
- config JSON 不含密钥，API 输出统一 bool/typed DTO。
- invalid config 不进入自动交易，返回可诊断错误且不改变账户资金。

## 前置与并行

前置 M02、M13、M19。与 M19 都涉及 model/repo，必须先约定表和 UoW 后串行合并。

