from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from benchmark.contracts import Market
from benchmark.infrastructure.market import (
    DisplayMarketDataService,
    TradingMarketDataService,
)
from benchmark.providers import (
    Freshness,
    HealthStatus,
    KlineQuery,
    KlineResult,
    MarketStatusResult,
    PriceResult,
)
from benchmark.testing import FakePriceCache


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class CountingMarketDataPort:
    id = "fake.market"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema = {"type": "object"}

    def __init__(self, price: PriceResult) -> None:
        self.price = price
        self.price_calls = 0
        self.status_calls = 0

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        self.price_calls += 1
        return self.price

    def get_klines(self, query: KlineQuery) -> KlineResult:
        return KlineResult((), self.id, Freshness.UNAVAILABLE, "unused")

    def get_market_status(self, symbol: str, market: Market) -> MarketStatusResult:
        self.status_calls += 1
        return MarketStatusResult(True, self.id, NOW)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


def test_trading_service_returns_fresh_cached_price_without_provider_price_call():
    cached = PriceResult(Decimal("101"), NOW, "cache", Freshness.FRESH)
    provider = CountingMarketDataPort(
        PriceResult(Decimal("102"), NOW, "provider", Freshness.FRESH)
    )
    cache = FakePriceCache()
    cache.set("BTC", Market.CRYPTO, cached)

    result = TradingMarketDataService(provider, price_cache=cache).require_price(
        "BTC",
        Market.CRYPTO,
    )

    assert result == cached
    assert provider.status_calls == 1
    assert provider.price_calls == 0


def test_trading_service_ignores_stale_cache_and_populates_fresh_price():
    stale = PriceResult(Decimal("100"), NOW, "cache", Freshness.STALE)
    fresh = PriceResult(Decimal("102"), NOW, "provider", Freshness.FRESH)
    provider = CountingMarketDataPort(fresh)
    cache = FakePriceCache()
    cache.set("BTC", Market.CRYPTO, stale)

    result = TradingMarketDataService(provider, price_cache=cache).require_price(
        "BTC",
        Market.CRYPTO,
    )

    assert result == fresh
    assert provider.price_calls == 1
    assert cache.get("BTC", Market.CRYPTO) == fresh


def test_display_service_returns_allowed_stale_cache_without_fallback():
    stale = PriceResult(Decimal("100"), NOW, "cache", Freshness.STALE)
    provider = CountingMarketDataPort(
        PriceResult(Decimal("102"), NOW, "provider", Freshness.FRESH)
    )
    cache = FakePriceCache()
    cache.set("BTC", Market.CRYPTO, stale)
    fallback_calls = []

    result = DisplayMarketDataService(
        provider,
        price_cache=cache,
        fallback=lambda *_: fallback_calls.append(True),
    ).get_price("BTC", Market.CRYPTO, allow_stale=True)

    assert result == stale
    assert provider.price_calls == 0
    assert fallback_calls == []


def test_display_service_ignores_stale_cache_when_stale_is_disallowed():
    stale = PriceResult(Decimal("100"), NOW, "cache", Freshness.STALE)
    fresh = PriceResult(Decimal("102"), NOW, "provider", Freshness.FRESH)
    provider = CountingMarketDataPort(fresh)
    cache = FakePriceCache()
    cache.set("BTC", Market.CRYPTO, stale)

    result = DisplayMarketDataService(provider, price_cache=cache).get_price(
        "BTC",
        Market.CRYPTO,
        allow_stale=False,
    )

    assert result == fresh
    assert provider.price_calls == 1
    assert cache.get("BTC", Market.CRYPTO) == fresh


def test_display_service_caches_fallback_without_calling_provider():
    fallback = PriceResult(Decimal("99"), NOW, "sql", Freshness.STALE)
    provider = CountingMarketDataPort(
        PriceResult(Decimal("102"), NOW, "provider", Freshness.FRESH)
    )
    cache = FakePriceCache()
    fallback_calls = []

    result = DisplayMarketDataService(
        provider,
        price_cache=cache,
        fallback=lambda *_: fallback_calls.append(True) or fallback,
    ).get_price("BTC", Market.CRYPTO, allow_stale=True)

    assert result == fallback
    assert fallback_calls == [True]
    assert provider.price_calls == 0
    assert cache.get("BTC", Market.CRYPTO) == fallback
