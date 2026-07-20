"""Market facades for strict trading and permissive display paths."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from benchmark.contracts import Market
from benchmark.providers import Freshness, MarketDataPort, PriceResult


@dataclass(frozen=True)
class TradingMarketDataService:
    market_data: MarketDataPort

    def require_price(self, symbol: str, market: Market) -> PriceResult:
        result = self.market_data.get_price(symbol, market)
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
        return result


@dataclass(frozen=True)
class DisplayMarketDataService:
    market_data: MarketDataPort

    def get_price(self, symbol: str, market: Market, allow_stale: bool = True) -> PriceResult:
        result = self.market_data.get_price(symbol, market)
        if allow_stale or result.freshness is not Freshness.STALE:
            return result
        return PriceResult(
            value=result.value,
            as_of=result.as_of,
            source=result.source,
            freshness=Freshness.UNAVAILABLE,
            error=result.error or "stale price is not allowed",
        )


__all__ = ["DisplayMarketDataService", "TradingMarketDataService"]
