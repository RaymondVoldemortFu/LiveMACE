# 开发与验证

安装、环境变量和开发服务入口见[项目 README](../README.md)，系统边界见[架构说明](architecture.md)。

## 本地检查

在 `backend/` 执行：

```sh
uv sync
DATABASE_URL=sqlite:// uv run pytest tests test -q
DATABASE_URL=sqlite:// uv run python scripts/generate_frontend_types.py --check
```

默认 pytest 配置排除 `integration`。需要外部模型、MySQL 或其他服务的测试应显式选择 `-m integration`，并提供该测试要求的配置。

在仓库根目录执行：

```sh
pnpm --dir frontend test
pnpm run build:frontend
git diff --check
```

修改 API DTO 后，从 `backend/` 运行 `uv run python scripts/generate_frontend_types.py` 更新前端类型。领域客户端与组件消费生成类型。

## 应用测试

应用工厂 `benchmark.bootstrap.app.create_app` 接受 `AppSettings` 和 `StartupMode`。单元测试使用 fake ports、临时数据库或注入的 UoW factory；HTTP 测试根据需求显式选择启动模式。

`backend/scripts/http_smoke.py` 提供临时 SQLite 的 HTTP / WebSocket 应用烟测。运行前在仓库根目录构建前端，再从 `backend/` 执行：

```sh
uv run python scripts/http_smoke.py
```

真实模型端到端验证脚本为 `backend/scripts/live_agent_check.py`。准备 Docker、provider 配置文件及脚本使用的 MySQL、Valkey、Agent 沙箱镜像后，从 `backend/` 执行：

```sh
uv run python scripts/live_agent_check.py --credentials .env --model YOUR_MODEL
```

该脚本创建独立容器和临时账户，运行四种 Agent 并核对事件、回执、账本、HTTP、WebSocket 和 MySQL 行锁。它会发起真实模型请求；证据写入 `.live-agent-check/`，退出时清理所创建的容器。`--serve-after` 可在自动检查通过后保留临时页面服务，停止服务后清理资源。

## 交易与扩展变更

- 交易写入经 `TradeCommandGateway`；计算和账本应用保持分离。
- 新增或修改财务行为时，检查成功、业务拒绝、异常回滚、幂等及账户并发语义。
- 扩展使用同步公开接口、显式能力声明和版本化配置；详见[扩展契约测试](extensions/contract-tests.md)。
- Prompt、工具 schema 与 provider 变化应覆盖其输入输出契约。

## 更新已有环境

安装入口与扩展配置见 [SDK 文档](extensions/README.md#runtime-configuration)。
默认 Compose 项目为 `livemace-bench`，使用 `livemace_bench` 数据库和 `livemace` 用户。
已有环境应通过 `COMPOSE_PROJECT_NAME` 或 `docker compose -p` 保留原项目名与数据卷，
并继续通过 `MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 和
`DATABASE_URL` 指向原有资源；MySQL 初始化变量只对空数据卷生效。
分析脚本可用 `--db`，离线评估可用 `--db-path` 或 `--database-url` 指定已有数据。
本机隔离栈、源数据库与历史备份保留既有资源身份。
