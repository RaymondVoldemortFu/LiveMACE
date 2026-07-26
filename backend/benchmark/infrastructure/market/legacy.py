"""Adapters around the existing market data implementation."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from benchmark.contracts import JsonValue, Market
from benchmark.providers import Freshness, HealthStatus, KlineQuery, KlineResult, MarketDataPort, PriceResult


class LegacyMarketDataAdapter(MarketDataPort):
    """Thin synchronous adapter preserving services.market_data behavior."""

    id = "core.market.legacy"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        try:
            from services import market_data
            from services.time_source import now_utc

            value = Decimal(str(market_data.get_last_price(symbol, market.value)))
            freshness = Freshness.FRESH if value > 0 else Freshness.UNAVAILABLE
            error = None if value > 0 else "price is not positive"
            return PriceResult(value=value, as_of=now_utc(), source=self.id, freshness=freshness, error=error)
        except Exception as exc:
            return PriceResult(value=None, as_of=None, source=self.id, freshness=Freshness.UNAVAILABLE, error=str(exc))

    def get_klines(self, query: KlineQuery) -> KlineResult:
        try:
            from services import market_data

            rows = market_data.get_kline_data(
                query.symbol,
                query.market.value,
                query.period,
                query.count,
                query.start_time,
                query.end_time,
            )
            return KlineResult(rows=tuple(rows), source=self.id, freshness=Freshness.FRESH)
        except Exception as exc:
            return KlineResult(rows=(), source=self.id, freshness=Freshness.UNAVAILABLE, error=str(exc))

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(status="ok", provider_id=self.id)


def create_default_market_data_port() -> MarketDataPort:
    return LegacyMarketDataAdapter()


__all__ = ["LegacyMarketDataAdapter", "create_default_market_data_port"]
