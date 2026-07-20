"""Synchronous market data provider port."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping, Protocol, Sequence

from benchmark.contracts import JsonValue, Market
from benchmark.contracts.common import _freeze_mapping, _require_aware, _require_non_empty

from .health import HealthStatus


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PriceResult:
    value: Decimal | None
    as_of: datetime | None
    source: str
    freshness: Freshness
    error: str | None = None

    def __post_init__(self) -> None:
        if self.value is not None and not isinstance(self.value, Decimal):
            raise TypeError("value must be Decimal or None")
        if self.as_of is not None:
            _require_aware(self.as_of, "as_of")
        _require_non_empty(self.source, "source")
        if not isinstance(self.freshness, Freshness):
            raise TypeError("freshness must be Freshness")
        if self.freshness is Freshness.UNAVAILABLE and not self.error:
            raise ValueError("unavailable price requires error")


@dataclass(frozen=True)
class KlineQuery:
    symbol: str
    market: Market
    period: str
    count: int = 100
    start_time: JsonValue = None
    end_time: JsonValue = None

    def __post_init__(self) -> None:
        _require_non_empty(self.symbol, "symbol")
        if not isinstance(self.market, Market):
            raise TypeError("market must be Market")
        _require_non_empty(self.period, "period")
        if not isinstance(self.count, int) or self.count <= 0:
            raise ValueError("count must be a positive integer")


@dataclass(frozen=True)
class KlineResult:
    rows: tuple[Mapping[str, JsonValue], ...]
    source: str
    freshness: Freshness
    error: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.rows, tuple):
            raise TypeError("rows must be a tuple")
        object.__setattr__(self, "rows", tuple(_freeze_mapping(row, "kline row") for row in self.rows))
        _require_non_empty(self.source, "source")
        if not isinstance(self.freshness, Freshness):
            raise TypeError("freshness must be Freshness")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))


class MarketDataPort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def get_price(self, symbol: str, market: Market) -> PriceResult: ...
    def get_klines(self, query: KlineQuery) -> KlineResult: ...
    def healthcheck(self) -> HealthStatus: ...


__all__ = ["Freshness", "KlineQuery", "KlineResult", "MarketDataPort", "PriceResult"]
