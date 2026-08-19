"""Adapter boundary for the historical SQL Kline cache."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, Sequence

from benchmark.providers import KlineQuery


class KlineCachePort(Protocol):
    def get(self, query: KlineQuery) -> tuple[Mapping[str, Any], ...]: ...
    def set(self, query: KlineQuery, rows: Sequence[Mapping[str, Any]]) -> None: ...


class LegacySqlKlineCacheAdapter(KlineCachePort):
    def __init__(
        self,
        loader: Callable[[str, str, str, int], Sequence[Mapping[str, Any]]],
        saver: Callable[[str, str, str, Sequence[Mapping[str, Any]]], None],
    ) -> None:
        self._loader = loader
        self._saver = saver

    def get(self, query: KlineQuery) -> tuple[Mapping[str, Any], ...]:
        if query.start_time is not None or query.end_time is not None:
            return ()
        return tuple(self._loader(query.symbol, query.market.value, query.period, query.count))

    def set(self, query: KlineQuery, rows: Sequence[Mapping[str, Any]]) -> None:
        self._saver(query.symbol, query.market.value, query.period, rows)


__all__ = ["KlineCachePort", "LegacySqlKlineCacheAdapter"]
