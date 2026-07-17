"""Dependency-free common DTOs used by public extension contracts."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, TypeAlias, Union
import re


JsonValue: TypeAlias = Union[
    None,
    bool,
    int,
    float,
    str,
    List["JsonValue"],
    Dict[str, "JsonValue"],
]

_IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")


class Market(str, Enum):
    CRYPTO = "CRYPTO"
    US = "US"


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


def _freeze_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _parse_bool_like(value: object) -> bool:
    """Parse legacy bool-like storage values for internal migration adapters."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"unsupported bool-like value: {value!r}")


@dataclass(frozen=True)
class ExtensionRef:
    id: str
    version: str
    api_version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.version, "version")
        if not self.id.isascii() or not _IDENTIFIER_RE.fullmatch(self.id):
            raise ValueError("id must be a lowercase ASCII namespaced identifier")
        if not isinstance(self.api_version, int) or self.api_version < 1:
            raise ValueError("api_version must be a positive integer")


@dataclass(frozen=True)
class AccountView:
    id: int
    name: str
    initial_capital: Decimal
    current_cash: Decimal
    frozen_cash: Decimal
    margin_used: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.id, int) or self.id <= 0:
            raise ValueError("id must be a positive integer")
        _require_non_empty(self.name, "name")
        for name in ("initial_capital", "current_cash", "frozen_cash", "margin_used"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"{name} must be Decimal")


@dataclass(frozen=True)
class PositionView:
    symbol: str
    market: Market
    quantity: Decimal
    available_quantity: Decimal
    avg_cost: Decimal
    leverage: int
    side: str | None

    def __post_init__(self) -> None:
        _require_non_empty(self.symbol, "symbol")
        if not isinstance(self.market, Market):
            raise TypeError("market must be a Market")
        for name in ("quantity", "available_quantity", "avg_cost"):
            if not isinstance(getattr(self, name), Decimal):
                raise TypeError(f"{name} must be Decimal")
        if not isinstance(self.leverage, int) or self.leverage <= 0:
            raise ValueError("leverage must be a positive integer")


@dataclass(frozen=True)
class PortfolioView:
    account: AccountView
    positions: tuple[PositionView, ...]
    prices: Mapping[str, Decimal]
    total_assets: Decimal
    captured_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.account, AccountView):
            raise TypeError("account must be AccountView")
        if not isinstance(self.positions, tuple) or not all(
            isinstance(position, PositionView) for position in self.positions
        ):
            raise TypeError("positions must be a tuple of PositionView")
        prices = _freeze_mapping(self.prices, "prices")
        for symbol, price in prices.items():
            _require_non_empty(symbol, "price symbol")
            if not isinstance(price, Decimal):
                raise TypeError("prices values must be Decimal")
        object.__setattr__(self, "prices", prices)
        if not isinstance(self.total_assets, Decimal):
            raise TypeError("total_assets must be Decimal")
        _require_aware(self.captured_at, "captured_at")


@dataclass(frozen=True)
class DecisionContext:
    account_id: int
    decision_round_id: str
    trace_id: str
    portfolio: PortfolioView
    config: Mapping[str, JsonValue]
    started_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        _require_non_empty(self.decision_round_id, "decision_round_id")
        _require_non_empty(self.trace_id, "trace_id")
        if not isinstance(self.portfolio, PortfolioView):
            raise TypeError("portfolio must be PortfolioView")
        object.__setattr__(self, "config", _freeze_mapping(self.config, "config"))
        _require_aware(self.started_at, "started_at")


def to_jsonable(value: Any) -> JsonValue:
    """Convert public DTO values to a deterministic JSON-compatible structure."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        _require_aware(value, "datetime")
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [to_jsonable(item) for item in value]
        return sorted(converted, key=repr)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


__all__ = [
    "JsonValue",
    "Market",
    "ExtensionRef",
    "AccountView",
    "PositionView",
    "PortfolioView",
    "DecisionContext",
    "to_jsonable",
]
