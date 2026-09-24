"""Post-round observation persistence, independent from trade settlement."""

from decimal import Decimal
from benchmark.contracts import to_jsonable
from benchmark.application.decisions.observability import redact
from database.connection import SessionLocal


def save_run_summary(account_id, result, prices, *, secrets=()):
    """Persist a round observation after tools have committed their own trades."""
    from database.models import Account, Position
    from services.ai_decision_service import save_ai_decision
    from services.asset_calculator import calculate_position_market_value

    decision = {
        "operation": "summary",
        "reason": redact(
            result.summary or f"Round finished: {result.termination_reason.value}.",
            secrets,
        ),
        "trace_id": result.trace_id,
        **{
            key: to_jsonable(value)
            for key, value in result.metadata.items()
            if key in {"compliance_audit", "llm_audit", "agent_reasoning"}
        },
    }
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise ValueError("Account no longer exists")
        positions = (
            db.query(Position)
            .filter(Position.account_id == account_id, Position.quantity != 0)
            .all()
        )
        # Reuse this round's quotes but read balances and positions after fills.
        # Summary persistence never performs a price request or submits an order.
        total = Decimal(account.current_cash)
        for position in positions:
            price = prices.get(position.symbol)
            if price is None or price <= 0:
                raise ValueError(f"Missing valuation price for {position.symbol}")
            total += Decimal(str(calculate_position_market_value(position, price)))
        save_ai_decision(
            db,
            account_id,
            decision,
            {"total_assets": total},
            executed=False,
            snapshot_prices=prices,
        )
