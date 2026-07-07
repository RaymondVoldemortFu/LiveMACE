---
rfc: "0001"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0000；AGENT.md；docs/agent_architecture.md"
---

# RFC-0001: 总体架构与模块边界重构

---

## Summary

本 RFC 定义 Open Alpha Arena 重构后的目标分层：前端控制台、API/WebSocket 边界、应用服务层、Agent 运行时、交易执行层、数据访问层、基础设施层。重构目标不是换框架，而是把现有可运行系统中的隐式副作用、跨层调用和重复语义收敛为稳定契约。

目标架构：

```text
frontend/app
  -> backend/api                 # HTTP/WS 契约层，薄路由
  -> backend/services            # 业务编排，拥有 use case
  -> backend/services/agent      # LLM Agent 与工具运行时
  -> backend/repositories        # DB 读写边界
  -> backend/database            # engine/session/model/migration helpers
  -> Redis / MySQL / Docker / external market & LLM providers
```

## Motivation

当前仓库已经形成清晰的业务能力，但边界仍然混杂：

- `backend/main.py` 同时承担 FastAPI 装配、建表、轻量迁移、默认数据种子、密钥迁移和服务启动。
- `services/trading_commands.py` 同时负责账户加载、行情读取、并发决策、baseline 执行、Agent/legacy 分流和交易落地。
- Agent 工具路径和 legacy JSON 决策路径并存，重复执行风险高。
- 前端 `frontend/app/lib/api.ts` 手写大量响应类型，缺少与后端 serializer 的统一契约。
- SQLite/MySQL、Redis、Docker、外部行情与 LLM provider 的可用性要求分散在代码和文档中。

如果不先冻结边界，后续拆分会把风险推向交易核心。

## Goals

- 明确每层拥有的状态、不变量和可调用方向。
- 把启动副作用、交易执行、Agent 工具、行情缓存、数据库迁移、API/WS、前端控制台拆成独立 RFC。
- 建立“契约先行、测试护栏先行、核心交易最后切”的重构节奏。
- 为后续迁移到 Alembic、类型生成、任务编排或 provider 插件化预留边界。

## Non-Goals

- 不在本 RFC 中改写业务代码。
- 不改变技术栈：仍为 FastAPI、SQLAlchemy、Vite/React、Redis、Docker Compose。
- 不引入新的撮合引擎或真实交易账户。
- 不把前端升级为交易事实源。

## Detailed Design

### 3.1 分层职责

| 层 | 职责 | 禁止事项 |
| --- | --- | --- |
| Frontend | 展示、控制、筛选、触发 API、消费 WS 快照 | 不执行交易、不推导资金状态为事实 |
| API/WS | 参数校验、响应序列化、鉴权/用户边界、错误码 | 不直接实现复杂交易逻辑 |
| Service | 用例编排、事务边界、后台任务、跨 repository 协调 | 不散落 SQL 细节 |
| Agent Runtime | Prompt、LLM 调用、工具注册、工具调用守卫、trace | 不直接绕过交易执行工具改仓位 |
| Trading Execution | 订单、成交、持仓、保证金、重复执行防线 | 不读取 UI 状态作为事实 |
| Repository | 数据库查询与写入 helper | 不承担业务决策 |
| Infrastructure | DB/Redis/Docker/外部 provider 连接与健康检查 | 不吞掉启动必需依赖错误 |

### 3.2 模块边界原则

- API route 只接收请求、调用 service、返回 DTO。
- Service 可调用 repository、Agent runtime、market data、execution service。
- Agent tool 执行交易必须经过 `trade_execution_tool.execute_trade_tool()` 或其重构后的同等入口。
- 定时任务必须以 service use case 为入口，不直接拼接 route 或前端响应类型。
- 任何跨线程 DB 使用必须创建独立 `SessionLocal()` 并在 finally 中关闭。

### 3.3 迁移策略

1. 为高风险路径补 characterization tests，尤其是工具模式交易不重复执行。
2. 新增 DTO/schema 层，先让 route 输出稳定。
3. 把 `main.py` 启动副作用迁到显式 startup bootstrap 模块。
4. 把 `trading_commands.py` 拆成账户加载、决策收集、执行分发、日志保存四组内部 API。
5. 将市场数据、缓存、外部 API 的失败语义统一成结构化结果。

### 3.4 不变量登记

- `protocol == "tool"` 或存在 `executed_trades` 的 Agent 决策不得二次执行。
- `get_last_price()` 返回 `0` 或 `None` 时必须 skip/reject，不得当作有效成交价。
- Crypto short 需要 leverage > 1。
- US 市场关闭时拒绝 US 交易，且 leverage 强制为 1。
- 同一 crypto symbol 只允许一个方向性仓位，不得悄悄合并相反方向或不同杠杆。
- WebSocket 快照是展示缓存，不是交易状态写入入口。

## Testing

- 在重构前后保留一组端到端 smoke：启动后 health、账户列表、行情读取、手动下单、AI 决策 dry-run、WS 连接。
- 针对边界补单元测试：route 不直接调用 execution、Agent tool 不绕过交易入口、repository 不依赖 FastAPI。
- 每个 RFC 落地 PR 必须说明影响到的全局不变量。

## Cross-Impact

- RFC-0002 负责启动副作用拆分。
- RFC-0003 与 RFC-0004 共同维护“工具模式交易不重复执行”。
- RFC-0007 与 RFC-0008 固化前后端契约。
- RFC-0010 把本 RFC 的测试矩阵落到开发命令和 CI 纪律。

