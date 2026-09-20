"""Narrow infrastructure adapter for legacy SQLAlchemy trading calculations.

Application services depend on this capability instead of a SQLAlchemy
Session. The raw session remains inside the persistence implementation and all
called legacy functions are forced into ``manage_transaction=False`` mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class TradeTransactionOperations(Protocol):
    def execute_trade(self, command: Any) -> Mapping[str, Any]: ...

    def create_order(self, **kwargs: Any) -> Any: ...

    def cancel_order(self, order: Any, *, reason: str) -> bool: ...

    def execute_order(self, order: Any) -> bool: ...

    def liquidate_if_required(self, account: Any, *, reason: str, is_cancelled=None) -> tuple[int, int]: ...


class SqlAlchemyTradeTransactionOperations:
    def __init__(self, session_provider: Callable[[], Session]) -> None:
        self._session_provider = session_provider

    def execute_trade(self, command):
        """Run the one fixed legacy bridge inside infrastructure.

        The executor is fixed by the infrastructure module; callers holding
        the application-facing transaction port cannot substitute a callback
        or obtain the underlying Session.
        """
        from benchmark.infrastructure.adapters.trade import execute_legacy_trade

        session = self._session_provider()
        # The transaction object itself exposes ``.session``. Keep both the
        # savepoint and its rollback decision private to infrastructure so the
        # application-facing port cannot recover or commit the raw Session.
        with session.begin_nested() as business_transaction:
            result = execute_legacy_trade(session, command)
            if not isinstance(result, Mapping) or result.get("executed") is not True:
                business_transaction.rollback()
            return result

    def create_order(self, **kwargs: Any) -> Any:
        from services.order_matching import create_order

        return create_order(db=self._session_provider(), **kwargs)

    def cancel_order(self, order: Any, *, reason: str) -> bool:
        from services.order_matching import cancel_order

        return cancel_order(
            self._session_provider(),
            order,
            reason=reason,
            manage_transaction=False,
            raise_on_error=True,
        )

    def execute_order(self, order: Any) -> bool:
        from services.order_matching import check_and_execute_order

        return check_and_execute_order(
            self._session_provider(),
            order,
            manage_transaction=False,
            raise_on_error=True,
        )


    def liquidate_if_required(self, account, *, reason, is_cancelled=None):
        """Evaluate risk and close positions inside the account-lock transaction."""
        from decimal import Decimal
        from uuid import uuid4
        from benchmark.contracts import Market, TradeCommand
        from database.models import Position
        from services.asset_calculator import calculate_position_market_value
        from services.market_data import get_trading_price
        from services.order_executor_leverage import _calculate_position_interest

        session = self._session_provider()
        positions = session.query(Position).filter(
            Position.account_id == account.id, Position.quantity > 0).all()
        leveraged = [p for p in positions if (p.leverage or 1) > 1]
        margin = Decimal(str(account.margin_used))
        if not leveraged or margin <= 0:
            return 0, 0
        equity = Decimal(str(account.current_cash))
        # Any missing/invalid quote aborts this entire transaction. Omitting a
        # positive asset value could otherwise turn a healthy account insolvent.
        for position in positions:
            if is_cancelled is not None and is_cancelled():
                return 0, 0
            price = Decimal(str(get_trading_price(position.symbol, position.market)))
            if not price.is_finite() or price <= 0:
                raise ValueError("Margin valuation requires a finite positive quote")
            equity += calculate_position_market_value(position, price)
            equity -= _calculate_position_interest(position)
        if equity / margin >= Decimal(str(account.maintenance_margin_ratio)):
            return 0, 0
        processed = executed = 0
        for position in leveraged:
            if is_cancelled is not None and is_cancelled():
                break
            command = TradeCommand(
                account_id=account.id, operation="close", market=Market(position.market),
                symbol=position.symbol, direction=(position.side or "LONG").lower(),
                sizing_mode="close_ratio", sizing_value=Decimal("1"), leverage=1,
                reason=reason, idempotency_key=f"liquidation:{uuid4()}",
            )
            processed += 1
            result = self.execute_trade(command)
            if result.get("executed") is not True:
                # Keep the account batch atomic if a close fails at execution.
                raise ValueError("Margin liquidation trade was rejected")
            executed += 1
        return processed, executed
