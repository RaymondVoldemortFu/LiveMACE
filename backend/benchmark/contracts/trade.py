"""Public benchmark trade command and execution result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .common import Market, _require_non_empty


@dataclass(frozen=True)
class TradeCommand:
    account_id: int
    operation: str
    market: Market
    symbol: str
    direction: str | None
    sizing_mode: str | None
    sizing_value: Decimal | None
    leverage: int
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if self.operation not in {"open", "close", "hold", "all_in", "close_all"}:
            raise ValueError("unsupported operation")
        if not isinstance(self.market, Market):
            raise TypeError("market must be a Market")
        if self.operation not in {"hold", "close_all"}:
            _require_non_empty(self.symbol, "symbol")
        if self.sizing_value is not None and not isinstance(self.sizing_value, Decimal):
            raise TypeError("sizing_value must be Decimal or None")
        if not isinstance(self.leverage, int) or self.leverage <= 0:
            raise ValueError("leverage must be a positive integer")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be str")
        _require_non_empty(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True)
class TradeCommandResult:
    accepted: bool
    executed: bool
    reject_code: str | None
    reject_message: str | None
    order_id: int | None
    trade_id: int | None
    normalized_command: TradeCommand

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool) or not isinstance(self.executed, bool):
            raise TypeError("accepted and executed must be bool")
        if not isinstance(self.normalized_command, TradeCommand):
            raise TypeError("normalized_command must be TradeCommand")
        if self.executed and not self.accepted:
            raise ValueError("an executed command must be accepted")
        if not self.accepted and not self.reject_code:
            raise ValueError("a rejected command requires reject_code")
        for name in ("order_id", "trade_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or None")


__all__ = ["TradeCommand", "TradeCommandResult"]
