"""SQLAlchemy adapters for the repository protocols (M19).

Unlike the legacy modules under ``backend/repositories`` (which commit
inside each helper), these adapters never commit: the owning UnitOfWork
controls the transaction. Query orderings mirror the legacy modules.
Legacy modules stay untouched; their call sites migrate per owning task.

``with_for_update`` degrades to a no-op on SQLite (single-writer lock),
and takes a real row lock on MySQL — matching the SQLite-fallback /
MySQL-production split in RFC-0000.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from benchmark.persistence.errors import PersistenceConflictError


class _RepositoryBase:
    """Resolve the owning session on every repository operation.

    The provider is the UoW lifecycle/thread guard. SQLAlchemy objects never
    leave the persistence adapters as an application-facing session facade.
    """

    def __init__(self, session_provider: Callable[[], Session]):
        self._session_provider = session_provider

    @property
    def _session(self) -> Session:
        return self._session_provider()


class SqlAlchemyAccountRepository(_RepositoryBase):
    def add(self, account):
        self._session.add(account)
        self._session.flush()
        return account

    def get(self, account_id: int):
        from database.models import Account

        return self._session.query(Account).filter(Account.id == account_id).first()

    def get_for_update(self, account_id: int):
        from database.models import Account

        return (
            self._session.query(Account)
            .filter(Account.id == account_id)
            .with_for_update()
            .first()
        )

    def list_active_ai_accounts(self) -> List:
        from database.models import Account

        return (
            self._session.query(Account)
            .filter(Account.is_active == "true", Account.account_type == "AI")
            .all()
        )

    def list_by_user(self, user_id: int, active_only: bool = True) -> List:
        from database.models import Account

        query = self._session.query(Account).filter(Account.user_id == user_id)
        if active_only:
            query = query.filter(Account.is_active == "true")
        return query.all()

    def update_cash(
        self,
        account_id: int,
        current_cash: float,
        frozen_cash: Optional[float] = None,
    ):
        account = self.get(account_id)
        if not account:
            return None
        account.current_cash = current_cash
        if frozen_cash is not None:
            account.frozen_cash = frozen_cash
        self._session.flush()
        return account

    def set_active(self, account_id: int, active: bool):
        # is_active is a bool-like string column ("true"/"false") by design.
        account = self.get(account_id)
        if not account:
            return None
        account.is_active = "true" if active else "false"
        self._session.flush()
        return account


class SqlAlchemyAccountRuntimeConfigRepository(_RepositoryBase):
    """Account extension configuration persistence (M12).

    Never commits; the owning UnitOfWork controls the transaction. ``upsert``
    relies on the unique ``account_id`` constraint so a second config for the
    same account is a conflict, not a duplicate.
    """

    def get(self, account_id: int):
        from database.models import AccountRuntimeConfig

        return (
            self._session.query(AccountRuntimeConfig)
            .filter(AccountRuntimeConfig.account_id == account_id)
            .first()
        )

    def get_for_update(self, account_id: int):
        from database.models import AccountRuntimeConfig

        return (
            self._session.query(AccountRuntimeConfig)
            .filter(AccountRuntimeConfig.account_id == account_id)
            .with_for_update()
            .first()
        )

    def upsert(self, config):
        self._session.add(config)
        self._session.flush()
        return config

    def list_all(self):
        from database.models import AccountRuntimeConfig

        return (
            self._session.query(AccountRuntimeConfig)
            .order_by(AccountRuntimeConfig.account_id.asc())
            .all()
        )



class SqlAlchemyPositionRepository(_RepositoryBase):

    def get(self, account_id: int, symbol: str, market: str):
        from database.models import Position

        return (
            self._session.query(Position)
            .filter(
                Position.account_id == account_id,
                Position.symbol == symbol,
                Position.market == market,
            )
            .first()
        )

    def list_by_account(self, account_id: int) -> List:
        from database.models import Position

        return (
            self._session.query(Position)
            .filter(Position.account_id == account_id)
            .all()
        )

    def upsert(self, position):
        self._session.add(position)
        self._session.flush()
        return position


class SqlAlchemyOrderRepository(_RepositoryBase):

    def add(self, order):
        self._session.add(order)
        self._session.flush()
        return order

    def get_by_no(self, order_no: str):
        from database.models import Order

        return self._session.query(Order).filter(Order.order_no == order_no).first()

    def get_by_no_for_update(self, order_no: str):
        from database.models import Order

        return (
            self._session.query(Order)
            .filter(Order.order_no == order_no)
            .with_for_update()
            .first()
        )

    def list_by_account(self, account_id: int) -> List:
        from database.models import Order

        return (
            self._session.query(Order)
            .filter(Order.account_id == account_id)
            .order_by(Order.created_at.desc())
            .all()
        )

    def list_pending_for_update(self, account_id: Optional[int] = None) -> List:
        from database.models import Order

        query = self._session.query(Order).filter(Order.status == "PENDING")
        if account_id is not None:
            query = query.filter(Order.account_id == account_id)
        return query.order_by(Order.id.asc()).with_for_update().all()

    def list_pending_account_ids(self) -> List[int]:
        from database.models import Order

        rows = (
            self._session.query(Order.account_id)
            .filter(Order.status == "PENDING")
            .distinct()
            .order_by(Order.account_id.asc())
            .all()
        )
        return [int(row[0]) for row in rows]


class SqlAlchemyTradeRepository(_RepositoryBase):

    def add(self, trade):
        self._session.add(trade)
        self._session.flush()
        return trade

    def list_by_account(self, account_id: int) -> List:
        from database.models import Trade

        return (
            self._session.query(Trade)
            .filter(Trade.account_id == account_id)
            .order_by(Trade.trade_time.desc())
            .all()
        )

    def list_by_order(self, order_id: int) -> List:
        from database.models import Trade

        return self._session.query(Trade).filter(Trade.order_id == order_id).all()


class SqlAlchemyTradeCommandReceiptRepository(_RepositoryBase):

    def get(self, account_id: int, idempotency_key: str):
        from database.models import TradeCommandReceipt

        return (
            self._session.query(TradeCommandReceipt)
            .filter(
                TradeCommandReceipt.account_id == account_id,
                TradeCommandReceipt.idempotency_key == idempotency_key,
            )
            .first()
        )

    def claim(self, account_id: int, idempotency_key: str, command_json: str):
        from database.models import TradeCommandReceipt

        receipt = TradeCommandReceipt(
            account_id=account_id,
            idempotency_key=idempotency_key,
            status="PENDING",
            command_json=command_json,
        )
        self._session.add(receipt)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise PersistenceConflictError(
                "trade command receipt claim conflicted"
            ) from exc
        return receipt

    def complete(self, receipt, result_json: str, completed_at: datetime):
        if receipt.status != "PENDING":
            raise ValueError("only a pending trade command receipt can be completed")
        receipt.status = "COMPLETED"
        receipt.result_json = result_json
        receipt.completed_at = completed_at
        self._session.flush()
        return receipt


class SqlAlchemyDecisionRepository(_RepositoryBase):

    def add(self, decision):
        self._session.add(decision)
        self._session.flush()
        return decision

    def get(self, decision_id: int):
        from database.models import AIDecisionLog

        return (
            self._session.query(AIDecisionLog)
            .filter(AIDecisionLog.id == decision_id)
            .first()
        )

    def list_by_account(self, account_id: int, limit: Optional[int] = None) -> List:
        from database.models import AIDecisionLog

        query = (
            self._session.query(AIDecisionLog)
            .filter(AIDecisionLog.account_id == account_id)
            .order_by(AIDecisionLog.decision_time.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return query.all()


class SqlAlchemyTraceRepository(_RepositoryBase):

    def add(self, step):
        self._session.add(step)
        self._session.flush()
        return step

    def list_by_trace_id(self, trace_id: str) -> List:
        from database.models import AgentTrace

        return (
            self._session.query(AgentTrace)
            .filter(AgentTrace.trace_id == trace_id)
            .order_by(AgentTrace.step_number.asc())
            .all()
        )

    def list_by_account(self, account_id: int, limit: Optional[int] = None) -> List:
        from database.models import AgentTrace

        query = (
            self._session.query(AgentTrace)
            .filter(AgentTrace.account_id == account_id)
            .order_by(AgentTrace.created_at.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return query.all()


class SqlAlchemySnapshotRepository(_RepositoryBase):

    def add(self, snapshot):
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def list_by_account(self, account_id: int, since: Optional[datetime] = None) -> List:
        from database.models import AccountSnapshot

        query = self._session.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id
        )
        if since is not None:
            query = query.filter(AccountSnapshot.ts >= since)
        return query.order_by(AccountSnapshot.ts.asc()).all()

    def latest(self, account_id: int):
        from database.models import AccountSnapshot

        return (
            self._session.query(AccountSnapshot)
            .filter(AccountSnapshot.account_id == account_id)
            .order_by(AccountSnapshot.ts.desc())
            .first()
        )


class SqlAlchemyEvaluationRepository(_RepositoryBase):

    def add(self, checkpoint):
        self._session.add(checkpoint)
        self._session.flush()
        return checkpoint

    def get_period(
        self,
        account_id: int,
        interval_seconds: int,
        period_end: datetime,
    ):
        from database.models import AgentPeriodCheckpoint

        return (
            self._session.query(AgentPeriodCheckpoint)
            .filter(
                AgentPeriodCheckpoint.account_id == account_id,
                AgentPeriodCheckpoint.interval_seconds == interval_seconds,
                AgentPeriodCheckpoint.period_end == period_end,
            )
            .first()
        )

    def list_by_account(
        self,
        account_id: int,
        interval_seconds: Optional[int] = None,
    ) -> List:
        from database.models import AgentPeriodCheckpoint

        query = self._session.query(AgentPeriodCheckpoint).filter(
            AgentPeriodCheckpoint.account_id == account_id
        )
        if interval_seconds is not None:
            query = query.filter(
                AgentPeriodCheckpoint.interval_seconds == interval_seconds
            )
        return query.order_by(AgentPeriodCheckpoint.period_end.asc()).all()


class SqlAlchemyUserRepository(_RepositoryBase):
    def add(self, user):
        self._session.add(user)
        self._session.flush()
        return user

    def get(self, user_id: int):
        from database.models import User

        return self._session.query(User).filter(User.id == user_id).first()

    def get_by_username(self, username: str):
        from database.models import User

        return self._session.query(User).filter(User.username == username).first()
