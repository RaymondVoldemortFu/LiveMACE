"""Adapter boundary for the process-local short-TTL price cache."""

from __future__ import annotations

from typing import Any, Protocol

from benchmark.contracts import Market
from benchmark.providers import PriceResult


class PriceCachePort(Protocol):
    def get(self, symbol: str, market: Market) -> PriceResult | None: ...
    def set(self, symbol: str, market: Market, result: PriceResult) -> None: ...
    def clear(self) -> None: ...


class LegacyPriceCacheAdapter(PriceCachePort):
    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def get(self, symbol: str, market: Market) -> PriceResult | None:
        return self._cache.get_result(symbol, market.value)

    def set(self, symbol: str, market: Market, result: PriceResult) -> None:
        self._cache.set_result(symbol, market.value, result)

    def clear(self) -> None:
        self._cache.clear()


__all__ = ["LegacyPriceCacheAdapter", "PriceCachePort"]
