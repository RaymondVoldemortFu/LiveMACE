"""Reusable in-memory cache fakes for boundary and lifecycle tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

from benchmark.contracts import Market
from benchmark.providers import KlineQuery, PriceResult


class FakePriceCache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, Market], PriceResult] = {}

    @staticmethod
    def _key(symbol: str, market: Market) -> tuple[str, Market]:
        return str(symbol).strip().upper(), market

    def get(self, symbol: str, market: Market) -> PriceResult | None:
        return self.entries.get(self._key(symbol, market))

    def set(self, symbol: str, market: Market, result: PriceResult) -> None:
        self.entries[self._key(symbol, market)] = result

    def clear(self) -> None:
        self.entries.clear()


class FakeKlineCache:
    def __init__(self) -> None:
        self.entries: dict[str, tuple[Mapping[str, Any], ...]] = {}

    @staticmethod
    def _key(query: KlineQuery) -> str:
        return json.dumps(
            {
                "symbol": query.symbol,
                "market": query.market.value,
                "period": query.period,
                "count": query.count,
                "start_time": query.start_time,
                "end_time": query.end_time,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )

    def get(self, query: KlineQuery) -> tuple[Mapping[str, Any], ...]:
        return self.entries.get(self._key(query), ())

    def set(self, query: KlineQuery, rows: Sequence[Mapping[str, Any]]) -> None:
        self.entries[self._key(query)] = tuple(rows)


class FakeToolCache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str | None, str], object] = {}
        self._locks: set[tuple[str, str | None, str]] = set()

    @staticmethod
    def _key(
        namespace: str,
        args: dict[str, Any],
        round_id: str | None,
    ) -> tuple[str, str | None, str]:
        encoded = json.dumps(
            args,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )
        return namespace, round_id, encoded

    def get(
        self,
        namespace: str,
        args: dict[str, Any],
        *,
        round_id: str | None = None,
    ) -> object:
        return self.entries.get(self._key(namespace, args, round_id))

    def set(
        self,
        namespace: str,
        args: dict[str, Any],
        value: object,
        *,
        ttl_seconds: int | None = None,
        round_id: str | None = None,
    ) -> None:
        del ttl_seconds
        self.entries[self._key(namespace, args, round_id)] = value

    @contextmanager
    def acquire_lock(
        self,
        namespace: str,
        args: dict[str, Any],
        *,
        round_id: str | None = None,
    ) -> Iterator[bool]:
        key = self._key(namespace, args, round_id)
        acquired = key not in self._locks
        if acquired:
            self._locks.add(key)
        try:
            yield acquired
        finally:
            if acquired:
                self._locks.remove(key)


__all__ = ["FakeKlineCache", "FakePriceCache", "FakeToolCache"]
