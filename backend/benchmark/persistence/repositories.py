"""Repository protocols for the persistence boundary (M19).

One protocol per domain, matching the UnitOfWork attributes in the module
task doc: accounts, positions, orders, trades, decisions, traces,
snapshots, evaluations (plus users, needed by seed/auth flows).

Rules (RFC-0006 §3.2 / M19 TODO):

- Repositories only do DB access: lookups, simple create/update/delete,
  filtered list queries, row-lock/transaction helpers.
- Repositories never call LLMs, market data, trading strategies, FastAPI,
  or WebSocket sends (enforced by an import-boundary test).
- All methods are synchronous; returning an awaitable is a contract
  violation.
- Methods return controlled entity handles (ORM objects inside the app
  boundary) or the DTOs in ``views.py``; public extensions never see ORM.
- Query orderings mirror the legacy ``backend/repositories`` modules.

``*_for_update`` methods take a row lock (SELECT ... FOR UPDATE on MySQL;
no-op on SQLite, whose writer lock covers it) for concurrent account
config updates and pending-order processing.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # ORM types for typing only; no runtime import side effects
    from database.models import (
        Account,
        AccountSnapshot,
        AgentPeriodCheckpoint,
        AgentTrace,
        AIDecisionLog,
        Order,
        Position,
        Trade,
        User,
    )


@runtime_checkable
class AccountRepository(Protocol):
    def get(self, account_id: int) -> Optional["Account"]: ...

    def get_for_update(self, account_id: int) -> Optional["Account"]: ...

    def list_active_ai_accounts(self) -> List["Account"]: ...

    def list_by_user(self, user_id: int, active_only: bool = True) -> List["Account"]: ...

    def update_cash(
        self,
        account_id: int,
        current_cash: float,
        frozen_cash: Optional[float] = None,
    ) -> Optional["Account"]: ...

    def set_active(self, account_id: int, active: bool) -> Optional["Account"]: ...


@runtime_checkable
class PositionRepository(Protocol):
    def get(self, account_id: int, symbol: str, market: str) -> Optional["Position"]: ...

    def list_by_account(self, account_id: int) -> List["Position"]: ...

    def upsert(self, position: "Position") -> "Position": ...


@runtime_checkable
class OrderRepository(Protocol):
    def add(self, order: "Order") -> "Order": ...

    def get_by_no(self, order_no: str) -> Optional["Order"]: ...

    def get_by_no_for_update(self, order_no: str) -> Optional["Order"]: ...

    def list_by_account(self, account_id: int) -> List["Order"]: ...

    def list_pending_for_update(self, account_id: Optional[int] = None) -> List["Order"]: ...


@runtime_checkable
class TradeRepository(Protocol):
    def add(self, trade: "Trade") -> "Trade": ...

    def list_by_account(self, account_id: int) -> List["Trade"]: ...

    def list_by_order(self, order_id: int) -> List["Trade"]: ...


@runtime_checkable
class DecisionRepository(Protocol):
    def add(self, decision: "AIDecisionLog") -> "AIDecisionLog": ...

    def get(self, decision_id: int) -> Optional["AIDecisionLog"]: ...

    def list_by_account(
        self, account_id: int, limit: Optional[int] = None
    ) -> List["AIDecisionLog"]: ...


@runtime_checkable
class TraceRepository(Protocol):
    def add(self, step: "AgentTrace") -> "AgentTrace": ...

    def list_by_trace_id(self, trace_id: str) -> List["AgentTrace"]: ...

    def list_by_account(
        self, account_id: int, limit: Optional[int] = None
    ) -> List["AgentTrace"]: ...


@runtime_checkable
class SnapshotRepository(Protocol):
    def add(self, snapshot: "AccountSnapshot") -> "AccountSnapshot": ...

    def list_by_account(
        self,
        account_id: int,
        since: Optional[datetime] = None,
    ) -> List["AccountSnapshot"]: ...

    def latest(self, account_id: int) -> Optional["AccountSnapshot"]: ...


@runtime_checkable
class EvaluationRepository(Protocol):
    def add(self, checkpoint: "AgentPeriodCheckpoint") -> "AgentPeriodCheckpoint": ...

    def get_period(
        self,
        account_id: int,
        interval_seconds: int,
        period_end: datetime,
    ) -> Optional["AgentPeriodCheckpoint"]: ...

    def list_by_account(
        self,
        account_id: int,
        interval_seconds: Optional[int] = None,
    ) -> List["AgentPeriodCheckpoint"]: ...


@runtime_checkable
class UserRepository(Protocol):
    def get(self, user_id: int) -> Optional["User"]: ...

    def get_by_username(self, username: str) -> Optional["User"]: ...
