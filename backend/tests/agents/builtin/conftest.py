"""Helpers for ReAct built-in Agent migration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from benchmark.contracts import AccountView, DecisionContext, PortfolioView, PositionView


def make_decision_context(
    *,
    account_id: int = 7,
    cash: str = "10000",
    prices: dict[str, str] | None = None,
    positions: tuple[PositionView, ...] = (),
    total_assets: str | None = None,
    trace_id: str = "trace-react",
    decision_round_id: str = "round-react",
    started_at: datetime | None = None,
) -> DecisionContext:
    now = started_at or datetime(2026, 8, 16, 4, 0, tzinfo=timezone.utc)
    price_map = prices or {"BTC": "50000"}
    cash_value = Decimal(cash)
    return DecisionContext(
        account_id=account_id,
        decision_round_id=decision_round_id,
        trace_id=trace_id,
        portfolio=PortfolioView(
            account=AccountView(
                id=account_id,
                name="react-test",
                initial_capital=Decimal("10000"),
                current_cash=cash_value,
                frozen_cash=Decimal("0"),
                margin_used=Decimal("0"),
            ),
            positions=positions,
            prices={symbol: Decimal(value) for symbol, value in price_map.items()},
            total_assets=Decimal(total_assets) if total_assets is not None else cash_value,
            captured_at=now,
        ),
        config={},
        started_at=now,
    )
