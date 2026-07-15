# M20：Market Facade 与 Cache Adapters

## 交付目标

让业务和工具通过 `MarketDataPort` 访问行情，并明确 Redis tool cache、SQL Kline cache、进程内 PriceCache 的不同职责；不替换 provider、不改变 symbol 和 fallback 行为。

## 文件边界

- 主改：`services/market_data.py`、`market_kline_service.py`、`hyperliquid_market_data.py`、`alpaca_market_data.py`、`tool_cache.py`、`price_cache.py`、`repositories/kline_repo.py`。
- 新增：`backend/alpha_arena/infrastructure/market/`、`cache/` adapters。
- 不修改交易规则、前端和调度周期。

## 内部接口

```python
class TradingMarketDataService:
    def require_price(self, symbol: str, market: Market) -> PriceResult

class DisplayMarketDataService:
    def get_price(self, symbol: str, market: Market, allow_stale: bool = True) -> PriceResult

class ToolCachePort(Protocol):
    def get(self, ...) -> object: ...
    def set(self, ...) -> None: ...
    def acquire_lock(self, ...): ...
```

`PriceResult` 必含 value、as_of、source、freshness、error；交易 service 只接受正数且 fresh/当前允许状态。

## TODO

- [ ] 将 symbol/market normalize 和支持列表集中到单一 registry，删除 `market_kline_service -> trading_commands` 反向 import。
- [ ] Hyperliquid/Alpaca adapter 实现 M09 port，保留请求和 fallback。
- [ ] SQL Kline cache 只存历史行情；Redis ToolCache 只做 decision-round 去重；PriceCache 只做进程短 TTL。
- [ ] cache key 包含 provider/component version 和规范化查询参数。
- [ ] 运行期 Redis 失败返回 miss/error 并记录；full startup mandatory 语义由 M18 保持。
- [ ] Kline sandbox 文件元数据由 M06 消费；market service 只返回数据，不写容器文件。
- [ ] 资产曲线允许 stale 的现有 fallback 明确放在 Display service，不污染交易 strict path。

## 验收

- provider 返回 0/None/异常时交易路径拒绝，展示/资产 fallback 与当前行为一致。
- 原 Kline upsert/freshness/cache tests 通过。
- `market_kline_service.py` 不 import `trading_commands.py`。
- 三类 cache 有独立 fake 和生命周期测试。

## 前置与并行

前置 M09；provider adapter、Kline repository、Redis cache、PriceCache 可并行。
