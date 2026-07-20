"""Built-in market provider adapters implementing MarketDataPort."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Mapping

from benchmark.contracts import JsonValue, Market
from benchmark.providers import Freshness, HealthStatus, KlineQuery, KlineResult, MarketDataPort, PriceResult
from services.time_source import now_utc


class _FunctionMarketDataAdapter(MarketDataPort):
    id = "core.market.function"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(
        self,
        *,
        provider_id: str,
        price_loader: Callable[[str], float | None],
        kline_loader: Callable[[str, str, int, object, object], list[dict] | None],
        supported_market: Market,
    ) -> None:
        self.id = provider_id
        self._price_loader = price_loader
        self._kline_loader = kline_loader
        self._supported_market = supported_market

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        if market is not self._supported_market:
            return PriceResult(None, None, self.id, Freshness.UNAVAILABLE, f"{self.id} does not support {market.value}")
        try:
            value = self._price_loader(symbol)
            if value is None:
                return PriceResult(None, None, self.id, Freshness.UNAVAILABLE, "provider returned no price")
            decimal_value = Decimal(str(value))
            freshness = Freshness.FRESH if decimal_value > 0 else Freshness.UNAVAILABLE
            return PriceResult(decimal_value, now_utc(), self.id, freshness, None if decimal_value > 0 else "provider returned non-positive price")
        except Exception as exc:
            return PriceResult(None, None, self.id, Freshness.UNAVAILABLE, str(exc))

    def get_klines(self, query: KlineQuery) -> KlineResult:
        if query.market is not self._supported_market:
            return KlineResult((), self.id, Freshness.UNAVAILABLE, f"{self.id} does not support {query.market.value}")
        try:
            rows = self._kline_loader(query.symbol, query.period, query.count, query.start_time, query.end_time)
            return KlineResult(tuple(rows or ()), self.id, Freshness.FRESH if rows else Freshness.UNAVAILABLE, None if rows else "provider returned no kline data")
        except Exception as exc:
            return KlineResult((), self.id, Freshness.UNAVAILABLE, str(exc))

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


class HyperliquidMarketDataAdapter(_FunctionMarketDataAdapter):
    def __init__(self) -> None:
        from services.hyperliquid_market_data import get_kline_data_from_hyperliquid, get_last_price_from_hyperliquid

        super().__init__(
            provider_id="core.market.hyperliquid",
            price_loader=get_last_price_from_hyperliquid,
            kline_loader=get_kline_data_from_hyperliquid,
            supported_market=Market.CRYPTO,
        )


class AlpacaMarketDataAdapter(_FunctionMarketDataAdapter):
    def __init__(self) -> None:
        from services.alpaca_market_data import get_kline_data_from_alpaca, get_last_price_from_alpaca

        super().__init__(
            provider_id="core.market.alpaca",
            price_loader=get_last_price_from_alpaca,
            kline_loader=get_kline_data_from_alpaca,
            supported_market=Market.US,
        )


__all__ = ["AlpacaMarketDataAdapter", "HyperliquidMarketDataAdapter"]
