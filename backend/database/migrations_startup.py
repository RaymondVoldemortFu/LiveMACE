"""Startup schema migration registry (M19, RFC-0006 §3.1).

Replaces the ad hoc ALTER TABLE blocks formerly inlined in ``main.py``
with a declarative, idempotent registry. Each migration declares a
dialect, a check, an apply, and whether failure is fatal. Executed by the
schema bootstrap stage (``benchmark.bootstrap.schema``) on every start.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

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


def _mysql_columns_have_types(
    table: str, expected: Dict[str, str]
) -> Callable[[Connection], bool]:
    """True when every column already has the target MySQL data type.

    Checked through ``information_schema`` so the widening DDL runs only
    when actually needed: repeating ``ALTER TABLE`` on every startup takes
    metadata locks and triggers implicit commits even when it is a no-op.
    """

    def check(conn: Connection) -> bool:
        from sqlalchemy import text

        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name"
            ),
            {"table_name": table},
        )
        actual = {str(row[0]).lower(): str(row[1]).lower() for row in rows}
        return all(
            actual.get(column.lower()) == data_type.lower()
            for column, data_type in expected.items()
        )

    return check


def _trade_command_receipts_exists(conn: Connection) -> bool:
    from sqlalchemy import inspect

    return inspect(conn).has_table("trade_command_receipts")


def _create_trade_command_receipts(conn: Connection) -> None:
    from database.models import TradeCommandReceipt

    TradeCommandReceipt.__table__.create(bind=conn, checkfirst=True)


def _scheduled_job_occurrences_exists(conn: Connection) -> bool:
    from sqlalchemy import inspect

    return inspect(conn).has_table("scheduled_job_occurrences")


def _create_scheduled_job_occurrences(conn: Connection) -> None:
    from database.models import ScheduledJobOccurrence

    ScheduledJobOccurrence.__table__.create(bind=conn, checkfirst=True)


def _scheduled_job_occurrences_uses_epoch_us(conn: Connection) -> bool:
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if not inspector.has_table("scheduled_job_occurrences"):
        return False
    columns = {
        column["name"]: column
        for column in inspector.get_columns("scheduled_job_occurrences")
    }
    epoch_column = columns.get("run_at_epoch_us")
    if epoch_column is None or epoch_column.get("nullable", True):
        return False
    if "run_date" in columns:
        return False
    return any(
        constraint.get("column_names") == ["job_id", "run_at_epoch_us"]
        for constraint in inspector.get_unique_constraints(
            "scheduled_job_occurrences"
        )
    )


def _upgrade_scheduled_job_occurrences_to_epoch_us(conn: Connection) -> None:
    """Upgrade the short-lived run_date schema without losing consumed rows."""
    from sqlalchemy import inspect, text

    if not inspect(conn).has_table("scheduled_job_occurrences"):
        _create_scheduled_job_occurrences(conn)
        return
    if conn.dialect.name == "sqlite":
        conn.execute(
            text(
                "CREATE TABLE scheduled_job_occurrences_epoch_us ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "job_id VARCHAR(255) NOT NULL, "
                "run_at_epoch_us INTEGER NOT NULL, "
                "consumed_at DATETIME NOT NULL, "
                "CONSTRAINT uix_scheduled_job_occurrence_key "
                "UNIQUE (job_id, run_at_epoch_us))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scheduled_job_occurrences_epoch_us "
                "(id, job_id, run_at_epoch_us, consumed_at) "
                "SELECT id, job_id, "
                "CAST(strftime('%s', substr(run_date, 1, 19)) AS INTEGER) * 1000000 + "
                "CASE WHEN instr(run_date, '.') > 0 "
                "THEN CAST(substr(run_date || '000000', instr(run_date, '.') + 1, 6) AS INTEGER) "
                "ELSE 0 END, "
                "consumed_at FROM scheduled_job_occurrences"
            )
        )
        conn.execute(text("DROP TABLE scheduled_job_occurrences"))
        conn.execute(
            text(
                "ALTER TABLE scheduled_job_occurrences_epoch_us "
                "RENAME TO scheduled_job_occurrences"
            )
        )
        return
    if conn.dialect.name == "mysql":
        # MySQL DDL implicitly commits. Every phase is therefore discovered
        # from schema state and individually retryable after interruption.
        inspector = inspect(conn)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("scheduled_job_occurrences")
        }
        if "run_at_epoch_us" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE scheduled_job_occurrences "
                    "ADD COLUMN run_at_epoch_us BIGINT NULL"
                )
            )

        inspector = inspect(conn)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("scheduled_job_occurrences")
        }
        if "run_date" in columns:
            # The legacy DATETIME stored a UTC wall clock without timezone.
            # TIMESTAMPDIFF compares two DATETIME values directly and is not
            # affected by the MySQL connection/session timezone.
            conn.execute(
                text(
                    "UPDATE scheduled_job_occurrences SET run_at_epoch_us = "
                    "TIMESTAMPDIFF(MICROSECOND, "
                    "CAST('1970-01-01 00:00:00' AS DATETIME), run_date) "
                    "WHERE run_at_epoch_us IS NULL"
                )
            )

            inspector = inspect(conn)
            for constraint in inspector.get_unique_constraints(
                "scheduled_job_occurrences"
            ):
                if constraint.get("column_names") != [
                    "job_id",
                    "run_at_epoch_us",
                ]:
                    name = constraint.get("name")
                    if name:
                        conn.exec_driver_sql(
                            "ALTER TABLE scheduled_job_occurrences "
                            f"DROP INDEX `{name.replace('`', '``')}`"
                        )
            conn.execute(
                text(
                    "ALTER TABLE scheduled_job_occurrences "
                    "DROP COLUMN run_date, "
                    "MODIFY run_at_epoch_us BIGINT NOT NULL"
                )
            )
        elif columns["run_at_epoch_us"].get("nullable", True):
            conn.execute(
                text(
                    "ALTER TABLE scheduled_job_occurrences "
                    "MODIFY run_at_epoch_us BIGINT NOT NULL"
                )
            )

        inspector = inspect(conn)
        if not any(
            constraint.get("column_names") == ["job_id", "run_at_epoch_us"]
            for constraint in inspector.get_unique_constraints(
                "scheduled_job_occurrences"
            )
        ):
            conn.execute(
                text(
                    "ALTER TABLE scheduled_job_occurrences "
                    "ADD CONSTRAINT uix_scheduled_job_occurrence_key "
                    "UNIQUE (job_id, run_at_epoch_us)"
                )
            )
        return
    raise RuntimeError(
        "scheduled occurrence epoch-us migration does not support dialect "
        f"{conn.dialect.name!r}"
    )


STARTUP_MIGRATIONS: List[StartupMigration] = [
    StartupMigration(
        migration_id="202608_scheduled_job_occurrences",
        dialect=None,
        is_applied=_scheduled_job_occurrences_exists,
        apply=_create_scheduled_job_occurrences,
        fatal=True,
    ),
    StartupMigration(
        migration_id="202608_scheduled_job_occurrences_epoch_us",
        dialect=None,
        is_applied=_scheduled_job_occurrences_uses_epoch_us,
        apply=_upgrade_scheduled_job_occurrences_to_epoch_us,
        fatal=True,
    ),
    StartupMigration(
        migration_id="202608_trade_command_receipts",
        dialect=None,
        is_applied=_trade_command_receipts_exists,
        apply=_create_trade_command_receipts,
        fatal=True,
    ),
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
        is_applied=_mysql_columns_have_types("ai_decision_logs", {"reason": "text"}),
        apply=_mysql_alter(
            "ALTER TABLE ai_decision_logs MODIFY COLUMN reason TEXT NOT NULL"
        ),
        fatal=False,
    ),
    StartupMigration(
        migration_id="202606_agent_traces_longtext",
        dialect="mysql",
        is_applied=_mysql_columns_have_types(
            "agent_traces",
            {
                "content": "longtext",
                "tool_calls": "longtext",
                "tool_output": "longtext",
            },
        ),
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
