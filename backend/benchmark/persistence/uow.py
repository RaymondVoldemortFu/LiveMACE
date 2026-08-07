"""Synchronous Unit of Work (M19).

Interface per the module task doc: the UoW is a synchronous context
manager exposing one repository per domain. SQLAlchemy and the Agent
workers are both synchronous, so there is no async session facade and no
thread switching inside repositories.

Contract enforced at runtime:

- the UoW owns and closes its session; repositories never commit;
- one UoW per account worker: an instance is bound to the thread that
  entered it and rejects use from any other thread;
- single-use: after ``__exit__`` it cannot be re-entered;
- the session factory must return a plain synchronous ``Session``.
"""

from __future__ import annotations

import functools
import threading
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from sqlalchemy.engine import Connection, Engine, Result
from sqlalchemy.engine import Transaction as EngineTransaction
from sqlalchemy.engine.result import FilterResult
from sqlalchemy.orm import Query, Session
from sqlalchemy.orm import SessionTransaction

from benchmark.persistence.repositories import (
    AccountRepository,
    DecisionRepository,
    EvaluationRepository,
    OrderRepository,
    PositionRepository,
    SnapshotRepository,
    TraceRepository,
    TradeRepository,
    TradeCommandReceiptRepository,
    UserRepository,
)


@runtime_checkable
class UnitOfWork(Protocol):
    accounts: AccountRepository
    positions: PositionRepository
    orders: OrderRepository
    trades: TradeRepository
    trade_command_receipts: TradeCommandReceiptRepository
    decisions: DecisionRepository
    traces: TraceRepository
    snapshots: SnapshotRepository
    evaluations: EvaluationRepository
    users: UserRepository

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, exc_type, exc, tb) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Creates an independent UnitOfWork per call (per account worker)."""

    def __call__(self) -> UnitOfWork: ...


class SqlAlchemyUnitOfWork:
    """Synchronous SQLAlchemy-backed UnitOfWork."""

    accounts: AccountRepository
    positions: PositionRepository
    orders: OrderRepository
    trades: TradeRepository
    trade_command_receipts: TradeCommandReceiptRepository
    decisions: DecisionRepository
    traces: TraceRepository
    snapshots: SnapshotRepository
    evaluations: EvaluationRepository
    users: UserRepository

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._session: Optional[Session] = None
        self._owner_thread: Optional[int] = None
        self._closed = False

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        if self._closed:
            raise RuntimeError("UnitOfWork is single-use and already closed")
        if self._session is not None:
            raise RuntimeError("UnitOfWork is not re-entrant")
        session = self._session_factory()
        if not isinstance(session, Session):
            raise TypeError(
                "session_factory must return a synchronous sqlalchemy.orm.Session, "
                f"got {type(session)!r}"
            )
        self._session = session
        self._owner_thread = threading.get_ident()

        from benchmark.persistence.sqlalchemy_repositories import (
            SqlAlchemyAccountRepository,
            SqlAlchemyDecisionRepository,
            SqlAlchemyEvaluationRepository,
            SqlAlchemyOrderRepository,
            SqlAlchemyPositionRepository,
            SqlAlchemySnapshotRepository,
            SqlAlchemyTraceRepository,
            SqlAlchemyTradeRepository,
            SqlAlchemyTradeCommandReceiptRepository,
            SqlAlchemyUserRepository,
        )

        guarded_session = GuardedSessionAccess(self._active_session)
        self.accounts = SqlAlchemyAccountRepository(guarded_session)
        self.positions = SqlAlchemyPositionRepository(guarded_session)
        self.orders = SqlAlchemyOrderRepository(guarded_session)
        self.trades = SqlAlchemyTradeRepository(guarded_session)
        self.trade_command_receipts = SqlAlchemyTradeCommandReceiptRepository(guarded_session)
        self.decisions = SqlAlchemyDecisionRepository(guarded_session)
        self.traces = SqlAlchemyTraceRepository(guarded_session)
        self.snapshots = SqlAlchemySnapshotRepository(guarded_session)
        self.evaluations = SqlAlchemyEvaluationRepository(guarded_session)
        self.users = SqlAlchemyUserRepository(guarded_session)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._session is not None
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
            self._closed = True

    def _active_session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of its context manager")
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError(
                "UnitOfWork is bound to its creating worker thread and must not "
                "be shared across threads"
            )
        return self._session

    def commit(self) -> None:
        self._active_session().commit()

    def rollback(self) -> None:
        self._active_session().rollback()

    @property
    def session(self) -> "GuardedSessionAccess":
        """Escape hatch for legacy call sites during migration.

        New code goes through the repository attributes; direct session
        use should shrink to zero as call sites migrate. The returned
        proxy re-checks the owner thread on *every* attribute access and
        wraps Connection/Transaction/Engine results so holding them cannot
        bypass the cross-thread guard.
        """
        # Validate context and calling thread eagerly, matching the old
        # behavior of raising at property access time.
        self._active_session()
        return GuardedSessionAccess(self._active_session)


def default_unit_of_work_factory(**session_kwargs: Any) -> UnitOfWorkFactory:
    """Factory bound to the application's ``SessionLocal``.

    Imports ``database.connection`` lazily so that importing this module
    never creates an engine.
    """

    def _factory() -> SqlAlchemyUnitOfWork:
        from database.connection import SessionLocal

        return SqlAlchemyUnitOfWork(lambda: SessionLocal(**session_kwargs))

    return _factory


def _guard_db_result(owner_check: Callable[[], Any], value: Any) -> Any:
    """Wrap SQLAlchemy objects that can execute or defer SQL."""

    if isinstance(value, Session):
        # Query.session / similar escapes must not hand out a bare Session.
        return GuardedSessionAccess(owner_check)
    if isinstance(
        value,
        (
            Connection,
            Engine,
            EngineTransaction,
            SessionTransaction,
            Query,
            Result,
            FilterResult,
        ),
    ):
        return _ThreadGuardedDbProxy(owner_check, value)
    return value


class _GuardedIterator:
    """Iterator that re-checks the UoW owner on every ``next``."""

    __slots__ = ("_owner_check", "_iterator")

    def __init__(self, owner_check: Callable[[], Any], iterator: Any) -> None:
        self._owner_check = owner_check
        self._iterator = iterator

    def __iter__(self) -> "_GuardedIterator":
        return self

    def __next__(self) -> Any:
        self._owner_check()
        return _guard_db_result(self._owner_check, next(self._iterator))


class _ThreadGuardedDbProxy:
    """Proxy DB objects so every use re-checks the UoW owner and lifecycle."""

    __slots__ = ("_owner_check", "_target")

    def __init__(self, owner_check: Callable[[], Any], target: Any) -> None:
        object.__setattr__(self, "_owner_check", owner_check)
        object.__setattr__(self, "_target", target)

    def __repr__(self) -> str:
        return f"_ThreadGuardedDbProxy({self._target!r})"

    def __getattr__(self, name: str) -> Any:
        self._owner_check()
        attribute = getattr(self._target, name)
        if not callable(attribute):
            return _guard_db_result(self._owner_check, attribute)

        @functools.wraps(attribute)
        def _guarded_call(*args: Any, **kwargs: Any) -> Any:
            self._owner_check()
            return _guard_db_result(
                self._owner_check, attribute(*args, **kwargs)
            )

        return _guarded_call

    def __iter__(self):
        self._owner_check()
        return _GuardedIterator(self._owner_check, iter(self._target))

    def __next__(self):
        self._owner_check()
        return _guard_db_result(self._owner_check, next(self._target))

    def __bool__(self) -> bool:
        self._owner_check()
        return bool(self._target)

    def __len__(self) -> int:
        self._owner_check()
        return len(self._target)

    def __getitem__(self, key: Any) -> Any:
        self._owner_check()
        return _guard_db_result(self._owner_check, self._target[key])

    def __enter__(self) -> Any:
        self._owner_check()
        entered = self._target.__enter__()
        # begin()/begin_nested() typically return self; keep the proxy.
        if entered is self._target:
            return self
        return _guard_db_result(self._owner_check, entered)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        self._owner_check()
        return self._target.__exit__(exc_type, exc, tb)


class GuardedSessionAccess:
    """Resolve every repository Session access through the UoW guard.

    Callable attributes (``execute``, ``query``, ...) are not handed out as
    bare bound methods: they are wrapped so the owner-thread/context check
    re-runs at *call* time. Connection/Transaction/Engine/Query/Result
    results are wrapped so a thread cannot capture them and use them from
    another thread or after the UoW closes.
    """

    def __init__(self, provider: Callable[[], Session]) -> None:
        self._provider = provider

    def __getattr__(self, name: str) -> Any:
        provider = self._provider
        attribute = getattr(provider(), name)
        if not callable(attribute):
            return _guard_db_result(provider, attribute)

        @functools.wraps(attribute)
        def _guarded_call(*args: Any, **kwargs: Any) -> Any:
            # Re-resolve through the provider so the owner-thread and
            # open-context checks run on every invocation, not only when
            # the attribute was first looked up.
            return _guard_db_result(
                provider, getattr(provider(), name)(*args, **kwargs)
            )

        return _guarded_call
