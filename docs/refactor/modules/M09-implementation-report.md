# M09 实现报告

## 交付范围

M09 已提供完整的同步 Provider 边界：

- `LLMClientPort` 只接收和返回 benchmark DTO，不向 Agent/Tool 暴露 OpenAI SDK 对象。
- `MemoryStorePort` 由同一个 `LegacyMemoryStoreAdapter` 包装 Local、Chroma 和 Pinecone 的既有 `MemoryInterface`，保留账户及市场 namespace。
- `MarketDataPort` 包含 price、kline、market status 和只读 healthcheck；Alpaca、Hyperliquid 与组合 legacy adapter 都实现同一接口。
- `SandboxPort` 包装 `ContainerService`，提供幂等 release 和异常安全的 `managed_lease()`。
- `AgentBuildContext.llm/prompts/tools` 已收紧为公开 Port/Resolver/Invoker 类型；legacy Agent factory 使用显式 LLM bridge。

## 失败语义

所有 adapter 都拒绝 coroutine/awaitable，不创建 event loop。外部异常转换为稳定 `ProviderError`，details 只记录 provider、operation 和异常类型，不复制可能含 key/token 的供应商错误文本。无效响应结构、无效 tool-call JSON、模型不匹配和不支持的选项均显式报错，不降级为空结果。

`LLMClient` 的 retry、Gemini schema/tool-call normalization、Grok token 参数和 timeout guardrail 仍由原实现负责；adapter 只做 DTO 转换和边界校验。

## 测试系统

`backend/tests/providers/` 覆盖：

- 四类 adapter 的同步返回与 awaitable 拒绝；
- LLM 参数转发、SDK 隔离、tool-call JSON 校验、错误脱敏；
- Market status、异常映射和不支持市场；
- Memory account/market namespace 与坏数据拒绝；
- Sandbox context 释放和重复释放；
- 四类公共 fake 的 Protocol 一致性；
- 公共 Provider import graph 不加载 OpenAI/Pinecone/Alpaca/Docker SDK。

真实网络访问不进入默认测试套件；provider 自身 retry/fallback 的真实网络验证应继续标记为 integration。
