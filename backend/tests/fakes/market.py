"""Deterministic market-data fake with explicit failure modes."""

from __future__ import annotations


class FakeMarketProvider:
    def __init__(self, prices=None, *, us_open: bool = True, error: Exception | None = None):
        self.prices = dict(prices or {})
        self.us_open = us_open
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def get_last_price(self, symbol: str, market: str = "CRYPTO"):
        self.calls.append((symbol, market))
        if self.error is not None:
            raise self.error
        return self.prices.get((symbol, market), self.prices.get(symbol))

    def get_market_status(self, symbol: str, market: str = "CRYPTO"):
        if self.error is not None:
            raise self.error
        return {"is_trading": True if market == "CRYPTO" else self.us_open}

