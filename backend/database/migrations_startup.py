"""Startup schema migration registry (M19, RFC-0006 §3.1).

Replaces the ad hoc ALTER TABLE blocks formerly inlined in ``main.py``
with a declarative, idempotent registry. Each migration declares a
dialect, a check, an apply, and whether failure is fatal. Executed by the
schema bootstrap stage (``benchmark.bootstrap.schema``) on every start.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartupMigration:
    """One idempotent startup migration.

    - ``migration_id``: stable id, e.g. ``202606_account_tool_routing_enabled``.
    - ``dialect``: "sqlite", "mysql", or None for dialect-independent.
    - ``is_applied``: returns True when the schema change already exists.
    - ``apply``: performs the change; only called when ``is_applied`` is False.
    - ``fatal``: True aborts startup on failure; False logs and continues.
    """

    migration_id: str
    dialect: Optional[str]
    is_applied: Callable[[Connection], bool]
    apply: Callable[[Connection], None]
    fatal: bool = False


def _sqlite_has_column(table: str, column: str) -> Callable[[Connection], bool]:
    def check(conn: Connection) -> bool:
        from sqlalchemy import text

        cols = [row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))]
        return column in cols

    return check


def _sqlite_add_column(table: str, ddl: str) -> Callable[[Connection], None]:
    def apply(conn: Connection) -> None:
        from sqlalchemy import text

        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

    return apply


def _mysql_alter(statement: str) -> Callable[[Connection], None]:
    def apply(conn: Connection) -> None:
        from sqlalchemy import text

        conn.execute(text(statement))

    return apply


def _never_applied(conn: Connection) -> bool:
    # MySQL widenings are themselves idempotent; failure (already widened)
    # is non-fatal, matching the old try/except-warn behavior in main.py.
    return False


STARTUP_MIGRATIONS: List[StartupMigration] = [
    StartupMigration(
        migration_id="202606_agent_checkpoint_volatility",
        dialect="sqlite",
        is_applied=_sqlite_has_column("agent_period_checkpoints", "volatility"),
        apply=_sqlite_add_column(
            "agent_period_checkpoints", "volatility FLOAT DEFAULT 0.0 NOT NULL"
        ),
        fatal=True,
    ),
    StartupMigration(
        migration_id="202606_account_tool_routing_enabled",
        dialect="sqlite",
        is_applied=_sqlite_has_column("accounts", "tool_routing_enabled"),
        apply=_sqlite_add_column(
            "accounts", "tool_routing_enabled VARCHAR(10) DEFAULT 'true' NOT NULL"
        ),
        fatal=True,
    ),
    StartupMigration(
        migration_id="202606_ai_decision_reason_text",
        dialect="mysql",
        is_applied=_never_applied,
        apply=_mysql_alter(
            "ALTER TABLE ai_decision_logs MODIFY COLUMN reason TEXT NOT NULL"
        ),
        fatal=False,
    ),
    StartupMigration(
        migration_id="202606_agent_traces_longtext",
        dialect="mysql",
        is_applied=_never_applied,
        apply=_mysql_alter(
            "ALTER TABLE agent_traces "
            "MODIFY COLUMN content LONGTEXT NULL, "
            "MODIFY COLUMN tool_calls LONGTEXT NULL, "
            "MODIFY COLUMN tool_output LONGTEXT NULL"
        ),
        fatal=False,
    ),
]


def run_startup_migrations(
    engine: Engine,
    migrations: Optional[List[StartupMigration]] = None,
) -> List[str]:
    """Run all registered migrations applicable to the engine's dialect.

    Returns the ids of migrations actually applied. Already-applied
    migrations are logged and skipped; failures follow the migration's
    ``fatal`` flag.
    """
    applied: List[str] = []
    for migration in STARTUP_MIGRATIONS if migrations is None else migrations:
        if migration.dialect is not None and engine.dialect.name != migration.dialect:
            continue
        try:
            with engine.begin() as conn:
                if migration.is_applied(conn):
                    logger.info("startup migration already applied: %s", migration.migration_id)
                    continue
                migration.apply(conn)
            applied.append(migration.migration_id)
            logger.info("startup migration applied: %s", migration.migration_id)
        except Exception as exc:
            if migration.fatal:
                logger.error("fatal startup migration %s failed: %s", migration.migration_id, exc)
                raise
            logger.warning(
                "startup migration %s failed (non-fatal): %s", migration.migration_id, exc
            )
    return applied
