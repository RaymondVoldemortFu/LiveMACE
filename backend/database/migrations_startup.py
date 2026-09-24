"""Startup schema migration registry (M19, RFC-0006 §3.1).

Replaces the ad hoc ALTER TABLE blocks formerly inlined in ``main.py``
with a declarative, idempotent registry. Each migration declares a
dialect, a check, an apply, and whether failure is fatal. Executed by the
schema bootstrap stage (``benchmark.bootstrap.schema``) on every start.
"""

from __future__ import annotations

import logging
import hashlib
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


def _mysql_timestamp_has_precision(
    table: str,
    column: str,
    precision: int,
) -> Callable[[Connection], bool]:
    """True when a MySQL timestamp column has the required FSP and nullability."""

    def check(conn: Connection) -> bool:
        from sqlalchemy import text

        row = conn.execute(
            text(
                "SELECT DATA_TYPE, DATETIME_PRECISION, IS_NULLABLE "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table_name "
                "AND COLUMN_NAME = :column_name"
            ),
            {"table_name": table, "column_name": column},
        ).first()
        if row is None:
            return False
        return (
            str(row[0]).lower() == "timestamp"
            and int(row[1] or 0) >= precision
            and str(row[2]).upper() == "NO"
        )

    return check


def _mysql_upgrade_runtime_config_timestamp(conn: Connection) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            "UPDATE account_runtime_configs SET updated_at = CURRENT_TIMESTAMP(6) "
            "WHERE updated_at IS NULL"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE account_runtime_configs MODIFY COLUMN updated_at "
            "TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
        )
    )


def _trade_command_receipts_exists(conn: Connection) -> bool:
    from sqlalchemy import inspect

    return inspect(conn).has_table("trade_command_receipts")


def _create_trade_command_receipts(conn: Connection) -> None:
    from database.models import TradeCommandReceipt

    TradeCommandReceipt.__table__.create(bind=conn, checkfirst=True)


def _account_runtime_configs_exists(conn: Connection) -> bool:
    from sqlalchemy import inspect

    return inspect(conn).has_table("account_runtime_configs")


def _create_account_runtime_configs(conn: Connection) -> None:
    from database.models import AccountRuntimeConfig

    AccountRuntimeConfig.__table__.create(bind=conn, checkfirst=True)


def _account_runtime_configs_backfilled(conn: Connection) -> bool:
    """True when every AI/legacy account already has a runtime-config row.

    Idempotent guard for the data backfill: it becomes True once no account is
    missing a config, so re-running the migration is a no-op. Manual accounts
    (which never ran an Agent) are excluded — they carry no meaningful Agent
    configuration to translate.

    Treated as already-applied when the source columns are absent (a minimal or
    partial ``accounts`` schema): there is nothing to translate, and the
    backfill must not crash on a schema it cannot read.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    if not inspector.has_table("account_runtime_configs") or not inspector.has_table(
        "accounts"
    ):
        # Nothing to backfill against yet; treated as applied.
        return True
    columns = {col["name"] for col in inspector.get_columns("accounts")}
    source_columns = {
        "id",
        "account_type",
        "agent_type",
        "memory_enabled",
        "tool_routing_enabled",
        "enable_rule_aware",
    }
    if not source_columns.issubset(columns):
        return True
    missing = conn.execute(
        text(
            "SELECT COUNT(*) FROM accounts a "
            "LEFT JOIN account_runtime_configs c ON c.account_id = a.id "
            "WHERE c.account_id IS NULL AND a.account_type = 'AI'"
        )
    ).scalar_one()
    return missing == 0


def _backfill_account_runtime_configs(conn: Connection) -> None:
    """Translate legacy account flags into runtime-config rows (idempotent).

    Reads each AI account without a config, maps its ``agent_type`` + flags to
    an ``AccountExtensionConfig`` via the shared mapping, records the resolved
    component versions (so traces are reproducible), and inserts one row.
    Accounts already carrying a config are skipped, so re-running never
    duplicates.
    """
    import json

    from sqlalchemy import text

    from benchmark.accounts.config import config_from_legacy_account
    from benchmark.accounts.validation import validate_extension_config

    rows = conn.execute(
        text(
            "SELECT a.id, a.agent_type, a.memory_enabled, a.tool_routing_enabled, "
            "a.enable_rule_aware FROM accounts a "
            "LEFT JOIN account_runtime_configs c ON c.account_id = a.id "
            "WHERE c.account_id IS NULL AND a.account_type = 'AI'"
        )
    ).all()

    for row in rows:
        legacy = _LegacyAccountRow(
            agent_type=row[1],
            memory_enabled=row[2],
            tool_routing_enabled=row[3],
            enable_rule_aware=row[4],
        )
        config = config_from_legacy_account(legacy)
        result = validate_extension_config(config)
        stored = result.resolved_config if result.valid else config
        conn.execute(
            text(
                "INSERT INTO account_runtime_configs ("
                "account_id, agent_id, agent_version, agent_config_json, "
                "toolset_ids_json, disabled_tools_json, prompt_profile_id, "
                "prompt_profile_version, component_versions_json, "
                "validation_status, validation_errors_json) VALUES ("
                ":account_id, :agent_id, :agent_version, :agent_config_json, "
                ":toolset_ids_json, :disabled_tools_json, :prompt_profile_id, "
                ":prompt_profile_version, :component_versions_json, "
                ":validation_status, :validation_errors_json)"
            ),
            {
                "account_id": row[0],
                "agent_id": stored.agent_id,
                "agent_version": stored.agent_version,
                "agent_config_json": json.dumps(dict(stored.agent_config)),
                "toolset_ids_json": json.dumps(list(stored.toolset_ids)),
                "disabled_tools_json": json.dumps(list(stored.disabled_tools)),
                "prompt_profile_id": stored.prompt_profile_id,
                "prompt_profile_version": stored.prompt_profile_version,
                "component_versions_json": json.dumps(dict(stored.component_versions)),
                "validation_status": result.status,
                "validation_errors_json": json.dumps(
                    [
                        {
                            "path": issue.path,
                            "message": issue.message,
                            "validator": issue.validator,
                        }
                        for issue in result.issues
                    ]
                ),
            },
        )


@dataclass(frozen=True)
class _LegacyAccountRow:
    """Attribute view over a raw accounts row for the shared legacy mapping."""

    agent_type: object
    memory_enabled: object
    tool_routing_enabled: object
    enable_rule_aware: object



def _scheduled_job_occurrences_exists(conn: Connection) -> bool:
    from sqlalchemy import inspect

    inspector = inspect(conn)
    # The epoch-us temp table is an owned intermediate phase of the following
    # migration. Treat it as existence here so the create migration cannot
    # manufacture an empty final table and shadow recoverable rows after an
    # interruption between DROP source and RENAME temp.
    return inspector.has_table(
        "scheduled_job_occurrences"
    ) or inspector.has_table("scheduled_job_occurrences_epoch_us")


def _create_scheduled_job_occurrences(conn: Connection) -> None:
    from database.models import ScheduledJobOccurrence

    ScheduledJobOccurrence.__table__.create(bind=conn, checkfirst=True)


def _scheduled_job_occurrences_uses_epoch_us(conn: Connection) -> bool:
    from sqlalchemy import inspect

    inspector = inspect(conn)
    if not inspector.has_table("scheduled_job_occurrences"):
        return False
    if inspector.has_table("scheduled_job_occurrences_epoch_us"):
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

    initial_inspector = inspect(conn)
    source_exists_initially = initial_inspector.has_table(
        "scheduled_job_occurrences"
    )
    temp_exists_initially = initial_inspector.has_table(
        "scheduled_job_occurrences_epoch_us"
    )
    if not source_exists_initially and not temp_exists_initially:
        _create_scheduled_job_occurrences(conn)
        return
    if conn.dialect.name == "sqlite":
        inspector = inspect(conn)
        source_exists = inspector.has_table("scheduled_job_occurrences")
        target_exists = inspector.has_table("scheduled_job_occurrences_epoch_us")
        if source_exists:
            source_columns = {
                column["name"]
                for column in inspector.get_columns("scheduled_job_occurrences")
            }
            if "run_at_epoch_us" in source_columns:
                if target_exists:
                    # A previous buggy/interrupted startup may have both an
                    # epoch-us final table and the recovery temp table. Merge
                    # by the occurrence identity before dropping temp; never
                    # infer "stale" merely from the final table's existence.
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO scheduled_job_occurrences "
                            "(id, job_id, run_at_epoch_us, consumed_at) "
                            "SELECT id, job_id, run_at_epoch_us, consumed_at "
                            "FROM scheduled_job_occurrences_epoch_us"
                        )
                    )
                    missing_count = conn.execute(
                        text(
                            "SELECT COUNT(*) "
                            "FROM scheduled_job_occurrences_epoch_us temp "
                            "LEFT JOIN scheduled_job_occurrences final "
                            "ON final.job_id = temp.job_id "
                            "AND final.run_at_epoch_us = temp.run_at_epoch_us "
                            "WHERE final.id IS NULL"
                        )
                    ).scalar_one()
                    if missing_count:
                        raise RuntimeError(
                            "scheduled occurrence recovery temp contains "
                            f"{missing_count} unmerged row(s)"
                        )
                    conn.execute(
                        text("DROP TABLE scheduled_job_occurrences_epoch_us")
                    )
                return
        if not target_exists:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS scheduled_job_occurrences_epoch_us ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "job_id VARCHAR(255) NOT NULL, "
                "run_at_epoch_us INTEGER NOT NULL, "
                "consumed_at DATETIME NOT NULL, "
                "CONSTRAINT uix_scheduled_job_occurrence_key "
                "UNIQUE (job_id, run_at_epoch_us))"
                )
            )
        if source_exists:
            # Re-running after an interrupted copy is safe: primary/unique
            # keys make every source row idempotent.
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO scheduled_job_occurrences_epoch_us "
                    "(id, job_id, run_at_epoch_us, consumed_at) "
                    "SELECT id, job_id, "
                    "CAST(strftime('%s', substr(run_date, 1, 19)) AS INTEGER) * 1000000 + "
                    "CASE WHEN instr(run_date, '.') > 0 "
                    "THEN CAST(substr(run_date || '000000', instr(run_date, '.') + 1, 6) AS INTEGER) "
                    "ELSE 0 END, "
                    "consumed_at FROM scheduled_job_occurrences"
                )
            )
            source_count = conn.execute(
                text("SELECT COUNT(*) FROM scheduled_job_occurrences")
            ).scalar_one()
            target_count = conn.execute(
                text("SELECT COUNT(*) FROM scheduled_job_occurrences_epoch_us")
            ).scalar_one()
            if source_count != target_count:
                raise RuntimeError(
                    "scheduled occurrence migration copy is incomplete: "
                    f"source={source_count}, target={target_count}"
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
        if not source_exists_initially:
            _create_scheduled_job_occurrences(conn)
            return
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
                if constraint.get("column_names") == [
                    "job_id",
                    "run_date",
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
        migration_id="202608_account_runtime_configs",
        dialect=None,
        is_applied=_account_runtime_configs_exists,
        apply=_create_account_runtime_configs,
        fatal=True,
    ),
    StartupMigration(
        migration_id="202608_account_runtime_configs_updated_at_fsp6",
        dialect="mysql",
        is_applied=_mysql_timestamp_has_precision(
            "account_runtime_configs", "updated_at", 6
        ),
        apply=_mysql_upgrade_runtime_config_timestamp,
        fatal=True,
    ),
    # Old SQLite databases may not have this source column yet. It must be
    # added before the all-dialect M12 backfill selects legacy account flags.
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
        migration_id="202608_account_runtime_configs_backfill",
        dialect=None,
        is_applied=_account_runtime_configs_backfilled,
        apply=_backfill_account_runtime_configs,
        fatal=False,
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
    from database.safety import validate_database_target

    validate_database_target(engine.url)
    applied: List[str] = []
    for migration in STARTUP_MIGRATIONS if migrations is None else migrations:
        if migration.dialect is not None and engine.dialect.name != migration.dialect:
            continue
        try:
            if engine.dialect.name == "sqlite":
                # Serialize schema discovery + DDL across concurrent app
                # startups. A deferred transaction can read stale phase state
                # before losing the write race; BEGIN IMMEDIATE acquires the
                # writer reservation first so every process discovers the
                # state committed by its predecessor.
                with engine.connect() as conn:
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        already_applied = migration.is_applied(conn)
                        if not already_applied:
                            migration.apply(conn)
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
            elif engine.dialect.name == "mysql":
                # MySQL DDL implicitly commits, so a transaction cannot close
                # the discovery/apply TOCTOU window. A connection-scoped
                # advisory lock survives those commits and serializes every
                # phase across concurrently starting application instances.
                from sqlalchemy import text

                with engine.connect() as conn:
                    database_name = (
                        conn.exec_driver_sql("SELECT DATABASE()").scalar_one_or_none()
                        or "default"
                    )
                    lock_identity = (
                        f"{database_name}:{migration.migration_id}".encode("utf-8")
                    )
                    lock_name = (
                        "open_alpha_migration_"
                        + hashlib.sha256(lock_identity).hexdigest()[:40]
                    )
                    acquired = conn.execute(
                        text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
                        {"lock_name": lock_name, "timeout_seconds": 30},
                    ).scalar_one()
                    conn.commit()
                    if acquired != 1:
                        raise RuntimeError(
                            "timed out acquiring MySQL startup migration lock "
                            f"for {migration.migration_id!r}"
                        )
                    operation_failed = False
                    try:
                        already_applied = migration.is_applied(conn)
                        if not already_applied:
                            migration.apply(conn)
                        conn.commit()
                    except BaseException:
                        operation_failed = True
                        conn.rollback()
                        raise
                    finally:
                        try:
                            released = conn.execute(
                                text("SELECT RELEASE_LOCK(:lock_name)"),
                                {"lock_name": lock_name},
                            ).scalar_one()
                            conn.commit()
                            if released != 1:
                                raise RuntimeError(
                                    "MySQL startup migration lock was not owned "
                                    f"for {migration.migration_id!r}"
                                )
                        except BaseException:
                            if operation_failed:
                                logger.exception(
                                    "failed to release MySQL migration lock after "
                                    "migration failure: %s",
                                    migration.migration_id,
                                )
                            else:
                                raise
            else:
                with engine.begin() as conn:
                    already_applied = migration.is_applied(conn)
                    if not already_applied:
                        migration.apply(conn)
            if already_applied:
                logger.info(
                    "startup migration already applied: %s",
                    migration.migration_id,
                )
                continue
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
