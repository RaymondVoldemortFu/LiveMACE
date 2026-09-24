# M12 实现报告

## 交付范围

用显式、版本化的账户扩展配置替换隐式的 `accounts.agent_type` + 布尔 flags 组合。新增 `account_runtime_configs` 表、公开 DTO、校验、乐观并发读写服务，以及幂等启动迁移（建表 + 数据回填）。旧列本阶段保留只读，待 M10/M21 把运行时读取切到新表后再单独删除。

## 数据模型

`AccountRuntimeConfig`（`database/models.py`），每账户一行（`account_id` 唯一 FK）：

- `agent_id` / `agent_version`：所选 Agent 组件与钉定版本。
- `agent_config_json`：经 schema 校验并写入默认值的 config 副本。
- `toolset_ids_json` / `disabled_tools_json`：所选 toolset 与逐账户禁用工具。
- `prompt_profile_id` / `prompt_profile_version`：所选 Prompt profile 与钉定版本（baseline 为空）。
- `component_versions_json`：钉定的组件版本，用于 trace 可复现（§11）。
- `validation_status`（`valid` | `configuration_invalid`）+ `validation_errors_json`：保存时的校验结论。

API key / model / base_url **不进**此表，仍留在 `accounts`，运行时单独解析（§9）。

## 公开接口

`benchmark.accounts`：

- `AccountExtensionConfig`：§9 公开 DTO，frozen、容器归一化为不可变；`to_dict()`/`from_dict()` 双向；不含任何 secret 字段。
- `config_from_legacy_account(account)`：把旧账户 flags 映射到等价 config，**行为逐字保持**：
  - `enable_rule_aware` 优先于 `agent_type`（与 `ai_decision_service` 一致：rule-aware 账户无论 `agent_type` 都跑 rule-aware Agent）。
  - react 的 prompt profile 由 `(memory_enabled, tool_routing_enabled)` 四象限选出，对齐 `benchmark.builtin.prompts` 的四个 react profile。
  - baseline（buy_hold/grid）不带 prompt profile。
- `validate_extension_config(config)`：对冻结的内置 registry 校验组件存在、版本可用、agent config schema、prompt profile 存在。引用缺失/未装载组件时返回 `configuration_invalid` 且 `resolved_config=None`，**不抛异常、不自动回退**（§9）。校验通过时把解析到的组件版本钉进 `component_versions` 并回填 schema 默认值。
- `get_runtime_config(uow, account_id)` / `save_runtime_config(uow, account_id, config, *, expected_updated_at)`：
  - 保存前先校验；invalid 也落库（标 `configuration_invalid`）而非拒绝，使账户停跑而非静默切换 Agent。
  - 乐观并发：`expected_updated_at` 必须等于当前行 `updated_at`（首次创建时须为 `None`），不匹配抛 `RuntimeConfigConflictError` 且不写入。
  - 读取结果和 `SaveResult` 都返回 timezone-aware UTC `updated_at` token；已有行不允许用默认 `None` 绕过乐观锁。
  - 两个操作都在调用方提供的同步 `UnitOfWork` 内运行，服务自身不开 session、不 commit——事务归 UoW，符合 G7“每个账户 worker 独立 UoW”。

## 持久化

`AccountRuntimeConfigRepository` 协议 + `SqlAlchemyAccountRuntimeConfigRepository`（`get`/`get_for_update`/`upsert`/`list_all`），挂到 `UnitOfWork.account_runtime_configs`。与其它 M19 adapter 一致：从不 commit，只 `flush()`；`get_for_update` 在 MySQL 取行锁、SQLite 退化为无操作。

## 迁移

`database/migrations_startup.py` 新增两条 dialect-independent 迁移，接现有 M19 startup 注册表：

1. `202608_account_runtime_configs`（fatal）：`checkfirst=True` 建表，幂等。
2. `202608_account_runtime_configs_backfill`（non-fatal）：对每个尚无 config 的 AI 账户，用共享映射翻译 flags、校验、钉版本后插入一行。guard `_account_runtime_configs_backfilled` 以“无缺失账户”为已应用条件，重复运行不重复插入。MANUAL 账户不回填（从未跑过 Agent）。SQLite/MySQL 共用同一 SQL（`LEFT JOIN` 检测缺失、参数化 `INSERT`）。

SQLite 的 `202606_account_tool_routing_enabled` 补列在回填前执行，确保旧库首次启动即可读取完整 legacy flags；回填 guard 同时检查所有源列。

MySQL 的 `updated_at` 使用 `TIMESTAMP(6) NOT NULL`，并由幂等迁移 `202608_account_runtime_configs_updated_at_fsp6` 升级已创建的旧表，避免微秒 token 被截断。

## API schema

`schemas/account.py` 新增 typed DTO：`AccountExtensionConfigDTO`、`AccountRuntimeConfigOut`（含 `validation_status` + typed 错误）、`AccountRuntimeConfigSave`（带 `expected_updated_at` 乐观锁）。均不含 secret，bool 为 typed 字段。HTTP router 接线归 M14/M21。

## 测试

`backend/tests/accounts/test_m12_runtime_config.py`：

- DTO：空 agent_id 拒绝、frozen 不可变、dict 双向、无 secret 字段。
- 映射：react 默认、四象限 profile 矩阵、rule-aware 优先级、multi-agent、baseline 无 profile。
- 校验：react 钉版本 + schema 默认；未知 agent / 未知 profile / 坏 schema / baseline 带多余 profile 均 `configuration_invalid`。
- 服务：UoW 暴露 repo、未设返回 None、保存-读取往返、UTC token、invalid 落库并标记、stale/缺失 token 乐观并发冲突、首次创建带 token 冲突。
- 迁移：建表 + 回填三类账户（react/rule-aware/baseline）、旧 SQLite 首次启动补列后立即回填、MySQL `TIMESTAMP(6)` DDL/幂等升级、重复运行不新增。

## 未做（归属其它模块）

- HTTP/WS router 接线（M14/M21）。
- 运行时读取从旧列切到新表、删除 `accounts` 上的 `agent_type`/`memory_enabled`/`tool_routing_enabled`/`enable_rule_aware`（M10/M21 切换后）。
- Toolset/capability 校验依赖 M13 Catalog；本波 `toolset_ids`/`disabled_tools` 仅结构化落库，未做逐工具存在性校验。
