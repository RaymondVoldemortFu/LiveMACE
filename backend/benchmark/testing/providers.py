"""Fake provider implementations for tests and third-party contract suites."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from benchmark.contracts import JsonValue, Market
from benchmark.providers import Freshness, HealthStatus, KlineQuery, KlineResult, MarketDataPort, PriceResult


class FakeMarketDataPort(MarketDataPort):
    id = "fake.market"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, price: Decimal | None = Decimal("100"), freshness: Freshness = Freshness.FRESH) -> None:
        self._price = price
        self._freshness = freshness

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        if self._price is None:
            return PriceResult(None, None, self.id, Freshness.UNAVAILABLE, "fake price unavailable")
        return PriceResult(self._price, datetime.now(timezone.utc), self.id, self._freshness)

    def get_klines(self, query: KlineQuery) -> KlineResult:
        return KlineResult(({"symbol": query.symbol, "market": query.market.value},), self.id, Freshness.FRESH)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = ["FakeMarketDataPort"]
