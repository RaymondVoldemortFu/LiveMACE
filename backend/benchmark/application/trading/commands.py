"""Strict internal order command DTOs used by the trading gateway."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from benchmark.contracts import Market


@dataclass(frozen=True)
class CreateOrderCommand:
    account_id: int
    symbol: str
    market: Market
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    leverage: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.market, Market):
            raise TypeError("market must be Market")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("order side must be BUY or SELL")
        if self.order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("unsupported order type")
        if not isinstance(self.quantity, Decimal) or self.quantity <= 0:
            raise ValueError("quantity must be a positive Decimal")
        if self.order_type == "LIMIT" and (
            not isinstance(self.price, Decimal) or self.price <= 0
        ):
            raise ValueError("LIMIT order requires a positive Decimal price")
        if self.price is not None and (
            not isinstance(self.price, Decimal) or self.price <= 0
        ):
            raise ValueError("price must be a positive Decimal or None")
        if not isinstance(self.leverage, int) or not 1 <= self.leverage <= 10:
            raise ValueError("leverage must be an integer between 1 and 10")
        if self.market is Market.US and self.leverage != 1:
            raise ValueError("US orders do not support leverage")


@dataclass(frozen=True)
class CancelOrderCommand:
    account_id: int
    order_no: str
    reason: str = "User cancelled"

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if not isinstance(self.order_no, str) or not self.order_no.strip():
            raise ValueError("order_no must be a non-empty string")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


@dataclass(frozen=True)
class OrderCommandResult:
    accepted: bool
    order_id: int | None = None
    trade_id: int | None = None
    reject_code: str | None = None
    reject_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise TypeError("accepted must be bool")
        if not self.accepted and not self.reject_code:
            raise ValueError("a rejected order command requires reject_code")
        for name in ("order_id", "trade_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or None")


@dataclass(frozen=True)
class ProcessPendingOrders:
    account_id: int | None = None

    def __post_init__(self) -> None:
        if self.account_id is not None and (
            not isinstance(self.account_id, int) or self.account_id <= 0
        ):
            raise ValueError("account_id must be a positive integer or None")


@dataclass(frozen=True)
class ProcessingResult:
    processed: int
    executed: int

    def __post_init__(self) -> None:
        if not isinstance(self.processed, int) or self.processed < 0:
            raise ValueError("processed must be a non-negative integer")
        if not isinstance(self.executed, int) or self.executed < 0:
            raise ValueError("executed must be a non-negative integer")
        if self.executed > self.processed:
            raise ValueError("executed must not exceed processed")


__all__ = [
    "CancelOrderCommand",
    "CreateOrderCommand",
    "OrderCommandResult",
    "ProcessPendingOrders",
    "ProcessingResult",
]
