"""Market facades for strict trading and permissive display paths."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from benchmark.contracts import Market
from benchmark.infrastructure.cache.price import PriceCachePort
from benchmark.providers import Freshness, MarketDataPort, PriceResult
from services.time_source import now_utc


DisplayFallback = Callable[[str, Market], PriceResult | None]


def _unavailable(port: MarketDataPort, message: str) -> PriceResult:
    return PriceResult(
        value=None,
        as_of=now_utc(),
        source=getattr(port, "id", "core.market"),
        freshness=Freshness.UNAVAILABLE,
        error=message,
    )


def _is_usable_display_price(result: PriceResult, allow_stale: bool) -> bool:
    if result.value is None or result.value <= Decimal("0"):
        return False
    if result.freshness is Freshness.FRESH:
        return True
    return allow_stale and result.freshness is Freshness.STALE


@dataclass(frozen=True)
class TradingMarketDataService:
    market_data: MarketDataPort
    price_cache: PriceCachePort | None = None

    def require_price(self, symbol: str, market: Market) -> PriceResult:
        try:
            status = self.market_data.get_market_status(symbol, market)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            return _unavailable(self.market_data, "market status is unavailable")
        if not status.is_trading:
            return _unavailable(
                self.market_data,
                status.reason or "market is not open for trading",
            )
        if self.price_cache is not None:
            cached = self.price_cache.get(symbol, market)
            if (
                cached is not None
                and cached.freshness is Freshness.FRESH
                and cached.value is not None
                and cached.value > Decimal("0")
            ):
                return cached
        try:
            result = self.market_data.get_price(symbol, market)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            return _unavailable(self.market_data, "market provider failed")
        if result.value is None or result.value <= Decimal("0"):
            return PriceResult(
                value=result.value,
                as_of=result.as_of,
                source=result.source,
                freshness=Freshness.UNAVAILABLE,
                error=result.error or "price is unavailable or non-positive",
            )
        if result.freshness is not Freshness.FRESH:
            return PriceResult(
                value=result.value,
                as_of=result.as_of,
                source=result.source,
                freshness=result.freshness,
                error=result.error or "fresh price is required for trading",
            )
        if self.price_cache is not None:
            self.price_cache.set(symbol, market, result)
        return result


@dataclass(frozen=True)
class DisplayMarketDataService:
    market_data: MarketDataPort
    price_cache: PriceCachePort | None = None
    fallback: DisplayFallback | None = None

    def get_price(self, symbol: str, market: Market, allow_stale: bool = True) -> PriceResult:
        if self.price_cache is not None:
            cached = self.price_cache.get(symbol, market)
            if cached is not None and _is_usable_display_price(cached, allow_stale):
                return cached
        if allow_stale and self.fallback is not None:
            fallback = self.fallback(symbol, market)
            if fallback is not None and _is_usable_display_price(fallback, allow_stale=True):
                if self.price_cache is not None:
                    self.price_cache.set(symbol, market, fallback)
                return fallback
        try:
            result = self.market_data.get_price(symbol, market)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            return _unavailable(self.market_data, "market provider failed")
        if _is_usable_display_price(result, allow_stale=False):
            if self.price_cache is not None:
                self.price_cache.set(symbol, market, result)
            return result
        if _is_usable_display_price(result, allow_stale=allow_stale):
            return result
        if allow_stale and self.fallback is not None:
            fallback = self.fallback(symbol, market)
            if fallback is not None and _is_usable_display_price(fallback, allow_stale=True):
                if self.price_cache is not None:
                    self.price_cache.set(symbol, market, fallback)
                return fallback
        return PriceResult(
            value=result.value,
            as_of=result.as_of,
            source=result.source,
            freshness=Freshness.UNAVAILABLE,
            error=result.error or "stale price is not allowed",
        )


__all__ = ["DisplayMarketDataService", "TradingMarketDataService"]
