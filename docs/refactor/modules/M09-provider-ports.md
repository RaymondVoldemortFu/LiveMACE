# M09：LLM、Memory、Market、Sandbox Provider Ports

## 交付目标

建立 Agent/Tool 依赖的同步基础设施接口，让第三方扩展只依赖 port，不直接依赖 OpenAI SDK、Pinecone、Alpaca、Hyperliquid 或 Docker SDK。本任务定义接口和现有实现 adapter 骨架，不改变 provider 行为。

## 文件边界

- 新增：`backend/alpha_arena/providers/{__init__,llm,memory,market,sandbox,health,errors}.py`。
- 新增：`backend/alpha_arena/infrastructure/adapters/`。
- 现有 provider 文件只允许加 adapter，不重写请求策略。

## 暴露接口

严格实现公共规范的 `LLMClientPort`、`MemoryStorePort`、`MarketDataPort`、`SandboxPort` 和 `HealthStatus`。Provider descriptor 必须含 `id`、`version`、`capabilities`、`config_schema`。

## TODO

- [ ] 定义 LLM request/response/tool-call DTO，隔离 OpenAI SDK object。
- [ ] 所有 Provider port 对系统暴露同步方法；现有同步实现不套 async adapter。
- [ ] Provider 返回 coroutine/awaitable 视为接口违规。第三方内部使用异步时必须自行运行并同步返回，系统不管理 event loop。
- [ ] `LLMClient` adapter 保留 retry、Gemini/Grok tool-call normalization 和错误状态码映射。
- [ ] 为 Local/Chroma/Pinecone memory 提供同一 adapter；account namespace 行为保持。
- [ ] 为 Hyperliquid/Alpaca 定义 market adapter；不在 port 中暴露 provider-specific feed 参数。
- [ ] 为 `ContainerService` 提供 lease/context adapter，确保释放幂等。
- [ ] 所有外部异常转 `ProviderError(code, retryable, provider_id)`，禁止泄露 key。
- [ ] healthcheck 不做写操作；规定 timeout，返回 ok/degraded/unavailable。
- [ ] fake ports 放在 `alpha_arena.testing`，供第三方契约测试复用。

## 验收

- Agent/Tool 公共包的 import graph 不包含 openai/pinecone/alpaca/docker。
- 每个现有 provider adapter 通过契约测试；真实网络测试标 integration。
- Provider 契约测试验证同步返回和 awaitable 拒绝。
- 现有 provider 的 timeout、retry、fallback、数据格式不变。

## 前置与并行

前置 M01。四类 port/adapter 可并行；M06、M20 使用这些接口。
