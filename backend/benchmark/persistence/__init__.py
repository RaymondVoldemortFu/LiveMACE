"""M19: Repository and Unit of Work boundary.

Layout (per module task doc M19):

- ``uow``: synchronous ``UnitOfWork`` protocol (one repository attribute
  per domain) + SQLAlchemy implementation + per-worker factory.
- ``repositories``: one protocol per domain; DB access only.
- ``views``: read-only DTOs — the only shapes public extensions see.
- ``sqlalchemy_repositories``: adapters; never commit (the UoW owns the
  transaction).

Importing this package must not create engines or sessions; concrete
wiring is resolved lazily inside factories.
"""

from benchmark.persistence.repositories import (
    AccountRepository,
    AccountRuntimeConfigRepository,
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
from benchmark.persistence.errors import PersistenceConflictError
from benchmark.persistence.uow import (
    SqlAlchemyUnitOfWork,
    UnitOfWork,
    UnitOfWorkFactory,
    UnitOfWorkState,
    default_unit_of_work_factory,
)

__all__ = [
    "AccountRepository",
    "AccountRuntimeConfigRepository",
    "PositionRepository",
    "OrderRepository",
    "TradeRepository",
    "TradeCommandReceiptRepository",
    "DecisionRepository",
    "TraceRepository",
    "SnapshotRepository",
    "EvaluationRepository",
    "UserRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "UnitOfWorkState",
    "SqlAlchemyUnitOfWork",
    "default_unit_of_work_factory",
    "PersistenceConflictError",
]
