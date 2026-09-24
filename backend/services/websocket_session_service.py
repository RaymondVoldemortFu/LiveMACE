"""Synchronous data/command boundary used by the async WebSocket adapter."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache
from typing import Any, Iterator, Mapping

from benchmark.application.trading import (
    CreateOrderCommand,
    get_default_trade_gateway,
)
from benchmark.contracts import Market
from database.connection import SessionLocal
from database.models import AIDecisionLog, Trade


@contextmanager
def open_websocket_session() -> Iterator[Any]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def recent_activity(db: Any, account_id: int, limit: int) -> tuple[list[Any], list[Any]]:
    trades = (
        db.query(Trade)
        .filter(Trade.account_id == account_id)
        .order_by(Trade.trade_time.desc())
        .limit(limit)
        .all()
    )
    decisions = (
        db.query(AIDecisionLog)
        .filter(AIDecisionLog.account_id == account_id)
        .order_by(AIDecisionLog.decision_time.desc())
        .limit(limit)
        .all()
    )
    return trades, decisions


@lru_cache(maxsize=1)
def _gateway():
    return get_default_trade_gateway()


def place_order(account_id: int, message: Mapping[str, Any]):
    command = CreateOrderCommand(
        account_id=account_id,
        symbol=str(message["symbol"]),
        market=Market(str(message.get("market", "CRYPTO"))),
        side=str(message["side"]),
        order_type=str(message["order_type"]),
        quantity=Decimal(str(message["quantity"])),
        price=(
            None
            if message.get("price") is None
            else Decimal(str(message["price"]))
        ),
        leverage=int(message.get("leverage", 1)),
    )
    return _gateway().create_order(command)


__all__ = ["open_websocket_session", "place_order", "recent_activity"]
