"""Narrow infrastructure adapter for legacy SQLAlchemy trading calculations.

Application services depend on this capability instead of a SQLAlchemy
Session. The raw session remains inside the persistence implementation and all
called legacy functions are forced into ``manage_transaction=False`` mode.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class TradeTransactionOperations(Protocol):
    def execute_trade(self, command: Any) -> Mapping[str, Any]: ...

    def savepoint(self) -> AbstractContextManager: ...

    def create_order(self, **kwargs: Any) -> Any: ...

    def cancel_order(self, order: Any, *, reason: str) -> bool: ...

    def execute_order(self, order: Any) -> bool: ...


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

        return execute_legacy_trade(self._session_provider(), command)

    def savepoint(self):
        return self._session_provider().begin_nested()

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
