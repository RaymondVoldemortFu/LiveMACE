---
rfc: "0002"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0001；backend/main.py；backend/services/startup.py；backend/services/scheduler.py"
---

# RFC-0002: 启动流程与调度生命周期重构

---

## Summary

本 RFC 规划把 `backend/main.py` 中的启动副作用拆成可测试的 bootstrap 管线，并把 scheduler、market task、auto trading、margin monitor、order scheduler、evaluation checkpoint 的生命周期统一登记、启动和关闭。

目标是让“导入 app”和“启动完整运行时”分离，降低测试、脚本和重构时误启动后台任务的风险。

## Motivation

`AGENT.md` 明确指出启动 `backend/main.py` 不是无副作用操作。当前 `on_startup()` 会执行：

- `Base.metadata.create_all()`
- SQLite `ALTER TABLE`
- MySQL 字段加宽
- 默认交易配置、默认用户、默认账户初始化
- placeholder credential 清理
- legacy plaintext api_key 加密迁移
- Redis tool cache 校验
- Docker sandbox 初始化
- scheduler、market tasks、asset curve backfill、auto trading、price cleanup、margin monitor、order scheduler、evaluation checkpoint 启动

这些动作对真实运行必要，但对单元测试、脚本和局部服务验证过重。

## Goals

- 把启动拆成 `schema_bootstrap`、`seed_bootstrap`、`secret_bootstrap`、`runtime_bootstrap` 四段。
- 让测试可以只初始化 DB schema，不启动 Redis/Docker/scheduler。
- 为每个后台任务定义唯一 task id、幂等启动、幂等停止和失败策略。
- 明确 Redis tool cache 是必需依赖，Docker sandbox 是功能依赖但可记录失败后继续。
- 清理 `startup.py` 中 ad hoc env parsing，集中到配置层。

## Non-Goals

- 不替换 APScheduler。
- 不改变默认自动交易周期。
- 不把任务系统迁移到 Celery/RQ。
- 不改变 Docker Compose 服务拓扑。

## Detailed Design

### 3.1 Bootstrap 分段

```text
create_app()
  -> register_middleware()
  -> register_routes()
  -> register_static()

startup_runtime(mode)
  -> schema_bootstrap()
  -> data_seed_bootstrap()
  -> credential_bootstrap()
  -> service_runtime_bootstrap()
```

`mode` 可取：

- `full`：生产/开发服务启动，执行全部步骤。
- `schema_only`：测试和脚本只建表/迁移。
- `no_background`：API 可启动，但不跑 scheduler 和自动交易。

### 3.2 Schema 与数据种子

把 `main.py` 中的建表、SQLite/MySQL 轻量迁移、默认数据写入迁移到 `backend/services/bootstrap/`。

建议模块：

- `schema.py`：`create_schema()`、`apply_startup_migrations(engine)`
- `seed.py`：`ensure_default_trading_configs()`、`ensure_default_user()`、`ensure_default_account()`
- `credentials.py`：placeholder 清理、legacy api_key 加密迁移

### 3.3 Runtime Service Registry

为后台任务引入登记表：

| task id | 当前入口 | 失败策略 |
| --- | --- | --- |
| scheduler | `start_scheduler()` | 必须成功 |
| market_tasks | `setup_market_tasks()` | 必须成功，失败阻止自动交易 |
| asset_curve_backfill_1h | `backfill_recent_1h_curve_on_startup()` | 记录错误，可继续 |
| ai_auto_trading | `reset_auto_trading_job()` | 必须成功 |
| price_cache_cleanup | `clear_expired_prices` | 可重试登记 |
| margin_monitor | `start_margin_monitor()` | 必须成功 |
| order_scheduler | `start_order_scheduler()` | 记录错误，可继续但健康检查降级 |
| eval_checkpoint_job | `run_checkpoint_jobs()` | 记录错误，可继续 |

### 3.4 幂等与关闭

- `initialize_services()` 重构为可重复调用：已启动 task 不重复登记。
- `shutdown_services()` 按启动逆序关闭。
- 每个外部资源在 shutdown 时必须记录成功/失败，不吞异常。
- FastAPI `on_event` 可保留，但内部只调用 bootstrap facade。

### 3.5 健康检查

扩展 `/api/health` 为轻量信息：

- `db: ok|degraded`
- `redis_tool_cache: ok|required_failed`
- `scheduler: running|stopped`
- `docker_sandbox: ready|unavailable`

默认 health 仍返回 200；新增 `/api/health/ready` 可用于部署 readiness，必需依赖失败时返回非 200。

## Testing

- `schema_only` 模式启动不会启动 scheduler。
- Redis 不可用时 full startup fail-fast。
- Docker 不可用时记录错误但 API 可启动，sandbox tool 返回明确错误。
- 重复调用 `initialize_services()` 不产生重复 APScheduler job。
- `shutdown_services()` 后后台 task 不再运行。

## Cross-Impact

- RFC-0006 负责把 startup migration 的 schema 细节规范化。
- RFC-0005 依赖 Redis tool cache readiness。
- RFC-0010 需要把 `schema_only` 和 `no_background` 模式接入 pytest fixture。

