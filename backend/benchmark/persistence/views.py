"""Read-only DTO views over persistence entities (M19).

These are the shapes exposed outside the application boundary (API
serializers, internal analytics). ORM entities never cross that line; the
``from_entity`` constructors here are the single conversion point.

Account, position, and portfolio shapes are owned by M01 and live in
``benchmark.contracts`` (``AccountView``, ``PositionView``,
``PortfolioView``); this module deliberately does NOT redefine them and
only holds the persistence-specific domains that M01 does not cover:
orders, trades, decisions, traces, snapshots, and evaluation checkpoints.

Financial fields keep their ``Decimal`` values as produced by the ORM;
serialization to float/str is the API layer's decision, not this one's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class OrderView:
    id: int
    order_no: str
    account_id: int
    symbol: str
    market: str
    side: str
    price: Optional[float]  # Order.price/quantity are Float columns
    quantity: float
    status: str
    created_at: Optional[datetime]

    @classmethod
    def from_entity(cls, order) -> "OrderView":
        return cls(
            id=order.id,
            order_no=order.order_no,
            account_id=order.account_id,
            symbol=order.symbol,
            market=order.market,
            side=order.side,
            price=order.price,
            quantity=order.quantity,
            status=order.status,
            created_at=order.created_at,
        )


@dataclass(frozen=True)
class TradeView:
    id: int
    order_id: int
    account_id: int
    symbol: str
    market: str
    side: str
    price: Decimal
    quantity: Decimal
    commission: Decimal
    trade_time: Optional[datetime]

    @classmethod
    def from_entity(cls, trade) -> "TradeView":
        return cls(
            id=trade.id,
            order_id=trade.order_id,
            account_id=trade.account_id,
            symbol=trade.symbol,
            market=trade.market,
            side=trade.side,
            price=trade.price,
            quantity=trade.quantity,
            commission=trade.commission,
            trade_time=trade.trade_time,
        )


@dataclass(frozen=True)
class DecisionView:
    id: int
    account_id: int
    operation: str
    symbol: Optional[str]
    direction: Optional[str]
    target_portion: Decimal
    executed: bool
    trace_id: Optional[str]
    decision_time: Optional[datetime]

    @classmethod
    def from_entity(cls, decision) -> "DecisionView":
        return cls(
            id=decision.id,
            account_id=decision.account_id,
            operation=decision.operation,
            symbol=decision.symbol,
            direction=decision.direction,
            target_portion=decision.target_portion,
            executed=str(decision.executed).lower() == "true",
            trace_id=decision.trace_id,
            decision_time=decision.decision_time,
        )


@dataclass(frozen=True)
class TraceStepView:
    id: int
    trace_id: str
    account_id: int
    step_number: int
    role: str
    content: Optional[str]
    created_at: Optional[datetime]

    @classmethod
    def from_entity(cls, step) -> "TraceStepView":
        return cls(
            id=step.id,
            trace_id=step.trace_id,
            account_id=step.account_id,
            step_number=step.step_number,
            role=step.role,
            content=step.content,
            created_at=step.created_at,
        )


@dataclass(frozen=True)
class SnapshotView:
    account_id: int
    ts: datetime
    total_equity: Decimal
    cash: Decimal
    positions_value: Decimal

    @classmethod
    def from_entity(cls, snapshot) -> "SnapshotView":
        return cls(
            account_id=snapshot.account_id,
            ts=snapshot.ts,
            total_equity=snapshot.total_equity,
            cash=snapshot.cash,
            positions_value=snapshot.positions_value,
        )


@dataclass(frozen=True)
class EvaluationCheckpointView:
    account_id: int
    interval_seconds: int
    period_start: datetime
    period_end: datetime
    equity_start: Decimal
    equity_end: Decimal
    pnl: Decimal
    return_rate: float
    volatility: float

    @classmethod
    def from_entity(cls, checkpoint) -> "EvaluationCheckpointView":
        return cls(
            account_id=checkpoint.account_id,
            interval_seconds=checkpoint.interval_seconds,
            period_start=checkpoint.period_start,
            period_end=checkpoint.period_end,
            equity_start=checkpoint.equity_start,
            equity_end=checkpoint.equity_end,
            pnl=checkpoint.pnl,
            return_rate=checkpoint.return_rate,
            volatility=checkpoint.volatility,
        )
