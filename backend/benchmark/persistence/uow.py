"""Synchronous Unit of Work boundary (M19).

The UoW owns exactly one SQLAlchemy transaction and exposes repositories plus
a narrow legacy trading transaction adapter. It deliberately does not expose a
Session/Engine/Connection object graph: application code cannot widen its own
database capabilities by traversing SQLAlchemy internals.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from benchmark.persistence.repositories import (
    AccountRepository,
    AccountRuntimeConfigRepository,
    DecisionRepository,
    EvaluationRepository,
    OrderRepository,
    PositionRepository,
    SnapshotRepository,
    TraceRepository,
    TradeCommandReceiptRepository,
    TradeRepository,
    UserRepository,
)
from benchmark.persistence.trade_transactions import TradeTransactionOperations


class UnitOfWorkState(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CLOSED = "closed"


@runtime_checkable
class UnitOfWork(Protocol):
    accounts: AccountRepository
    account_runtime_configs: AccountRuntimeConfigRepository
    positions: PositionRepository
    orders: OrderRepository
    trades: TradeRepository
    trade_command_receipts: TradeCommandReceiptRepository
    decisions: DecisionRepository
    traces: TraceRepository
    snapshots: SnapshotRepository
    evaluations: EvaluationRepository
    users: UserRepository
    trade_operations: TradeTransactionOperations

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, exc_type, exc, tb) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Creates an independent UnitOfWork per call/account worker."""

    def __call__(self) -> UnitOfWork: ...


class SqlAlchemyUnitOfWork:
    """Single-use, single-thread synchronous SQLAlchemy Unit of Work."""

    accounts: AccountRepository
    account_runtime_configs: AccountRuntimeConfigRepository
    positions: PositionRepository
    orders: OrderRepository
    trades: TradeRepository
    trade_command_receipts: TradeCommandReceiptRepository
    decisions: DecisionRepository
    traces: TraceRepository
    snapshots: SnapshotRepository
    evaluations: EvaluationRepository
    users: UserRepository
    trade_operations: TradeTransactionOperations

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._session: Optional[Session] = None
        self._owner_thread: Optional[int] = None
        self._state = UnitOfWorkState.NEW

    @property
    def state(self) -> UnitOfWorkState:
        return self._state

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        if self._state != UnitOfWorkState.NEW:
            raise RuntimeError(
                "UnitOfWork is single-use and cannot be entered from state "
                f"{self._state.value!r}"
            )
        session: Optional[Session] = None
        try:
            from sqlalchemy.orm import Session as SyncSession

            candidate = self._session_factory()
            if not isinstance(candidate, SyncSession):
                raise TypeError(
                    "session_factory must return a synchronous "
                    f"sqlalchemy.orm.Session, got {type(candidate)!r}"
                )
            # Do not treat arbitrary factory results as cleanup-capable
            # Sessions. Only a validated synchronous Session becomes owned.
            session = candidate
            self._session = session
            self._owner_thread = threading.get_ident()
            self._state = UnitOfWorkState.ACTIVE
            self._build_adapters()
            return self
        except BaseException:
            self._state = UnitOfWorkState.FAILED
            try:
                if session is not None:
                    try:
                        session.rollback()
                    finally:
                        session.close()
            finally:
                # Cleanup failures must never leave a reusable-looking UoW or
                # an owner-thread binding behind.
                self._session = None
                self._owner_thread = None
                self._state = UnitOfWorkState.CLOSED
            raise

    def _build_adapters(self) -> None:
        from benchmark.persistence.sqlalchemy_repositories import (
            SqlAlchemyAccountRepository,
            SqlAlchemyAccountRuntimeConfigRepository,
            SqlAlchemyDecisionRepository,
            SqlAlchemyEvaluationRepository,
            SqlAlchemyOrderRepository,
            SqlAlchemyPositionRepository,
            SqlAlchemySnapshotRepository,
            SqlAlchemyTraceRepository,
            SqlAlchemyTradeCommandReceiptRepository,
            SqlAlchemyTradeRepository,
            SqlAlchemyUserRepository,
        )
        from benchmark.persistence.trade_transactions import (
            SqlAlchemyTradeTransactionOperations,
        )

        provider = self._active_session
        self.accounts = SqlAlchemyAccountRepository(provider)
        self.account_runtime_configs = SqlAlchemyAccountRuntimeConfigRepository(
            provider
        )
        self.positions = SqlAlchemyPositionRepository(provider)
        self.orders = SqlAlchemyOrderRepository(provider)
        self.trades = SqlAlchemyTradeRepository(provider)
        self.trade_command_receipts = SqlAlchemyTradeCommandReceiptRepository(provider)
        self.decisions = SqlAlchemyDecisionRepository(provider)
        self.traces = SqlAlchemyTraceRepository(provider)
        self.snapshots = SqlAlchemySnapshotRepository(provider)
        self.evaluations = SqlAlchemyEvaluationRepository(provider)
        self.users = SqlAlchemyUserRepository(provider)
        self.trade_operations = SqlAlchemyTradeTransactionOperations(provider)

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError(
                "UnitOfWork is bound to its creating worker thread and must not "
                "be shared across threads"
            )

    def _active_session(self) -> Session:
        if self._state != UnitOfWorkState.ACTIVE or self._session is None:
            raise RuntimeError(
                "UnitOfWork database operation was attempted outside an active "
                "transaction; "
                f"current state is {self._state.value!r}"
            )
        self._assert_owner()
        return self._session

    def commit(self) -> None:
        session = self._active_session()
        try:
            session.commit()
        except BaseException:
            self._state = UnitOfWorkState.FAILED
            try:
                session.rollback()
            except BaseException:
                pass
            raise
        self._state = UnitOfWorkState.COMMITTED

    def rollback(self) -> None:
        if self._state == UnitOfWorkState.ROLLED_BACK:
            return
        if self._state != UnitOfWorkState.ACTIVE or self._session is None:
            raise RuntimeError(
                "UnitOfWork rollback requires an active transaction; "
                f"current state is {self._state.value!r}"
            )
        self._assert_owner()
        try:
            self._session.rollback()
        except BaseException:
            self._state = UnitOfWorkState.FAILED
            raise
        self._state = UnitOfWorkState.ROLLED_BACK

    def __exit__(self, exc_type, exc, tb) -> None:
        self._assert_owner()
        session = self._session
        if session is None:
            raise RuntimeError("UnitOfWork exited without an owned session")
        try:
            # Every path that did not explicitly commit is rolled back. This is
            # intentional even for a normal return and does not rely on
            # Session.close() side effects.
            if self._state in (UnitOfWorkState.ACTIVE, UnitOfWorkState.FAILED):
                try:
                    session.rollback()
                finally:
                    self._state = UnitOfWorkState.ROLLED_BACK
        finally:
            try:
                session.close()
            finally:
                self._session = None
                self._owner_thread = None
                self._state = UnitOfWorkState.CLOSED


def default_unit_of_work_factory(**session_kwargs: Any) -> UnitOfWorkFactory:
    """Factory bound lazily to the application's synchronous SessionLocal."""

    def _factory() -> SqlAlchemyUnitOfWork:
        from database.connection import SessionLocal

        return SqlAlchemyUnitOfWork(lambda: SessionLocal(**session_kwargs))

    return _factory
