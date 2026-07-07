---
rfc: "0005"
status: "Draft"
version: "v0.1"
author: "@refactor-planning"
reviewers:
  - "TBD"
created: "2026-06-19"
updated: "2026-06-19"
related: "RFC-0002；RFC-0003；backend/services/market_data.py；backend/services/tool_cache.py"
---

# RFC-0005: 行情、缓存与外部数据重构

---

## Summary

本 RFC 规划市场数据与缓存层重构。目标是统一 Crypto、US、Kline、价格缓存、Redis tool cache 和外部 API 的可用性语义，让交易路径能可靠地区分“有效价格”“暂时不可用”和“配置不支持”。

## Motivation

交易系统对行情质量高度敏感。`AGENT.md` 已明确：`0` 或 `None` 通常是 skip/reject 条件，不是有效交易价格。当前行情路径分散在：

- `services.market_data`
- `services.hyperliquid_market_data`
- `services.alpaca_market_data`
- `services.market_kline_service`
- `services.price_cache`
- repository cache tables
- Redis `tool_cache`

外部 API 失败、Redis 不可用、US feed credential 缺失、Kline 文件写入失败等情况需要有一致的返回和日志。

## Goals

- 定义统一 `MarketDataResult`，替代“异常、0、None、空 dict”混合语义。
- 将交易价格读取与展示价格读取分层，交易路径更严格。
- Redis tool cache 启动 fail-fast，运行期读写失败可按工具路径降级并记录。
- 为 Crypto/US 支持列表和 market 推断建立单一来源。
- 把 Kline 缓存与 sandbox 文件分析的生命周期记录清楚。

## Non-Goals

- 不替换外部行情 provider。
- 不扩大交易 symbol 列表。
- 不把 Redis 变成可选依赖。
- 不改变前端图表库。

## Detailed Design

### 3.1 MarketDataResult

```python
MarketDataResult = {
    "ok": bool,
    "symbol": str,
    "market": "CRYPTO" | "US",
    "price": Decimal | None,
    "timestamp": datetime | None,
    "source": "cache" | "hyperliquid" | "alpaca" | "fallback",
    "error_code": str | None,
    "error_message": str | None,
}
```

交易路径只接受 `ok == True` 且 `price > 0`。

### 3.2 价格读取分层

| 场景 | 函数语义 |
| --- | --- |
| 自动交易执行 | strict，失败返回 reject |
| 前端行情展示 | best-effort，可显示 stale 标记 |
| 资产曲线计算 | 可使用最近缓存，但需标记数据时间 |
| Agent market snapshot | 可缓存，但必须说明缺失 symbol |

### 3.3 Symbol Registry

整合：

- `AI_TRADING_SYMBOLS`
- `SUPPORTED_CRYPTO_SYMBOLS`
- Alpaca `SUPPORTED_STOCKS`
- 前端 crypto selector 数据

目标是新增 `services/trading_symbols.py` 作为后端单一 registry，API 暴露给前端，避免前端写死。

### 3.4 Redis Tool Cache

约束：

- `initialize_services()` 调用 `tool_cache.ensure_ready()`，失败直接阻止 full startup。
- 每个 decision round 使用 `decision_round_id` 隔离缓存 key。
- tool cache 内部读写失败不得让交易工具误认为获得有效数据；必须返回 tool error 或 cache miss。
- cleanup job 的 task id 固定为 `price_cache_cleanup` 或迁移后的等价名称。

### 3.5 Kline 与 Sandbox

Kline history 工具会把数据保存进 sandbox 供文件分析。重构后需要：

- 明确文件命名和清理策略。
- 记录 symbol、market、interval、row_count、data_source。
- 文件写入失败时，Agent 工具返回明确错误，不继续让模型读取不存在路径。

## Testing

- provider 返回 0/None/异常时，交易路径全部 reject。
- 展示路径可返回 stale 数据并带状态。
- Redis 不可用时 full startup 失败。
- Redis 运行期 set/get 失败时工具返回 cache miss/error，不伪造成功。
- US feed credential 缺失时 baseline 不刷屏逐 symbol warning，只输出聚合 warning。

## Cross-Impact

- RFC-0003 使用 strict price result 作为执行前置条件。
- RFC-0004 的 market snapshot tool 需要消费 `MarketDataResult`。
- RFC-0008 前端需要展示 stale/unavailable 状态。

