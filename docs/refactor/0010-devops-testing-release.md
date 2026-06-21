---
rfc: "0010"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "package.json；backend/pyproject.toml；docker-compose.yml；README.md；AGENT.md"
---

# RFC-0010: 开发体验、部署与测试重构

---

## Summary

本 RFC 规划开发命令、环境变量、Docker Compose、测试分层和发布检查。目标是让重构过程可以持续验证，而不是每次依赖手工启动全套服务。

## Motivation

仓库当前命令清晰：

```bash
pnpm run install:all
pnpm run dev
pnpm run build
cd backend && uv run pytest
docker compose up -d --build
```

但测试依赖差异较大：有些单元测试可本地运行，有些需要 Redis/MySQL/Docker/Pinecone/外部 LLM/Alpaca/Tavily。重构前必须把测试矩阵和环境要求写清楚。

## Goals

- 定义本地快速检查、后端完整检查、前端检查、集成检查四档。
- 把外部 provider 测试用 pytest marker 或环境检测隔离。
- Docker Compose readiness 明确依赖 MySQL、Redis、backend、frontend。
- 环境变量文档化并区分 required、optional、integration-only。
- 避免把生成产物、数据库、日志、缓存加入 git。

## Non-Goals

- 不建设完整 CI 平台细节。
- 不替换 uv/pnpm。
- 不改变服务端口默认值 5611/5621。
- 不发布 Python package。

## Detailed Design

### 3.1 检查分层

| 档位 | 命令 | 目的 |
| --- | --- | --- |
| fast | `cd backend && uv run pytest test/<focused>` | 单模块回归 |
| backend | `cd backend && uv run pytest` | 后端完整本地测试 |
| frontend | `pnpm run build:frontend` | TypeScript/Vite 构建 |
| full dev | `pnpm run dev` | 本地联调 |
| compose | `docker compose up -d --build` | 部署拓扑验证 |

### 3.2 Pytest Marker

建议 marker：

- `unit`
- `integration`
- `requires_redis`
- `requires_mysql`
- `requires_docker`
- `requires_llm`
- `requires_alpaca`
- `requires_pinecone`
- `requires_tavily`

默认 `uv run pytest` 跳过真实外部 provider；显式环境变量开启 integration。

### 3.3 环境变量分级

Required for full backend:

- `TOOL_CACHE_REDIS_URL`
- `API_KEY`
- `BASE_URL`

Production recommended:

- `DATABASE_URL`
- `API_KEY_CIPHER_KEY`
- DB pool 参数

Feature-specific:

- `PINECONE_API_KEY`
- `ALPACA_KEY`
- `ALPACA_SECRET`
- `AUDIT_*`
- `EVAL_LLM_*`
- `GEMINI_OPENAI_COMPAT_*`

### 3.4 Docker Compose

Compose readiness：

```text
mysql healthy
redis healthy
backend ready (/api/health/ready)
frontend serving and proxying /api /ws
```

后端容器继续挂载 `/var/run/docker.sock` 供 sandbox 工具使用，但文档中必须明确安全边界和本地开发风险。

### 3.5 生成产物纪律

不加入 git：

- `backend/data.db`
- `backend/logs/`
- `backend/static/`
- `frontend/dist/`
- `.env`
- cache/vector DB folders
- Docker artifacts
- 大型 eval 结果，除非明确作为样例 fixture

### 3.6 重构验收模板

每个落地 PR 需要说明：

- 覆盖 RFC 编号。
- 改动的运行域。
- 是否触及交易执行语义。
- 是否触及 schema/migration。
- 运行过的测试命令。
- 未覆盖风险。

## Testing

- `pnpm run build:frontend` 在无后端运行时可通过。
- `uv run pytest` 默认不要求外部 LLM provider。
- Redis required 测试在缺 Redis 时 skip 或明确 fail，不能半失败。
- Docker Compose 启动后 `/api/health/ready` 可用于 readiness。
- `.gitignore` 覆盖生成产物。

## Cross-Impact

- RFC-0002 的启动模式需要测试 fixture 支持。
- RFC-0006 的 SQLite/MySQL 差异需要测试矩阵支持。
- RFC-0007/RFC-0008 的契约生成需要前端 build 检查。
- RFC-0009 的 provider 评测需要 integration marker。

