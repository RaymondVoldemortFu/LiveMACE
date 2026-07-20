"""Trade command DTOs used by the gateway."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from benchmark.contracts import Market, TradeCommandResult


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


@dataclass(frozen=True)
class OrderCommandResult:
    accepted: bool
    order_id: int | None = None
    trade_id: int | None = None
    reject_code: str | None = None
    reject_message: str | None = None


@dataclass(frozen=True)
class ProcessPendingOrders:
    account_id: int | None = None


@dataclass(frozen=True)
class ProcessingResult:
    processed: int
    executed: int


__all__ = ["CreateOrderCommand", "OrderCommandResult", "ProcessPendingOrders", "ProcessingResult", "TradeCommandResult"]
