"""M19: UnitOfWork, repository, views and startup-migration contract tests."""

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database.connection import Base
from database.models import Account, User
from benchmark.persistence import (
    AccountRepository,
    DecisionRepository,
    EvaluationRepository,
    OrderRepository,
    PositionRepository,
    SnapshotRepository,
    SqlAlchemyUnitOfWork,
    TraceRepository,
    TradeRepository,
    UnitOfWork,
    UserRepository,
)
from benchmark.persistence.views import DecisionView


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _seed_account(session_factory) -> int:
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = User(username="u1", is_active="true")
        uow.users.add(user)
        account = Account(
            user_id=user.id,
            version="v1",
            name="A1",
            account_type="AI",
            initial_capital=10000.0,
            current_cash=10000.0,
            frozen_cash=0.0,
            is_active="true",
        )
        uow.accounts.add(account)
        uow.commit()
        return account.id


def test_uow_exposes_all_domain_repositories(session_factory):
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert isinstance(uow, UnitOfWork)
        assert isinstance(uow.accounts, AccountRepository)
        assert isinstance(uow.positions, PositionRepository)
        assert isinstance(uow.orders, OrderRepository)
        assert isinstance(uow.trades, TradeRepository)
        assert uow.trade_command_receipts is not None
        assert isinstance(uow.decisions, DecisionRepository)
        assert isinstance(uow.traces, TraceRepository)
        assert isinstance(uow.snapshots, SnapshotRepository)
        assert isinstance(uow.evaluations, EvaluationRepository)
        assert isinstance(uow.users, UserRepository)


def test_commit_persists(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.accounts.get(account_id) is not None
        assert uow.users.get_by_username("u1") is not None


def test_exception_rolls_back(session_factory):
    account_id = _seed_account(session_factory)
    with pytest.raises(RuntimeError, match="boom"):
        with SqlAlchemyUnitOfWork(session_factory) as uow:
            uow.accounts.update_cash(account_id, current_cash=1.0)
            raise RuntimeError("boom")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert float(uow.accounts.get(account_id).current_cash) == 10000.0


def test_business_rejection_rollback_discards_changes(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.accounts.update_cash(account_id, current_cash=1.0)
        uow.rollback()  # business-level rejection, no exception
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert float(uow.accounts.get(account_id).current_cash) == 10000.0


def test_uow_is_single_use(session_factory):
    uow = SqlAlchemyUnitOfWork(session_factory)
    with uow:
        pass
    with pytest.raises(RuntimeError, match="single-use"):
        with uow:
            pass


def test_uow_rejects_use_outside_context(session_factory):
    uow = SqlAlchemyUnitOfWork(session_factory)
    with pytest.raises(RuntimeError, match="outside"):
        uow.commit()


def test_uow_rejects_cross_thread_use(session_factory):
    errors = []
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        def use_from_other_thread():
            try:
                uow.commit()
            except RuntimeError as exc:
                errors.append(str(exc))

        t = threading.Thread(target=use_from_other_thread)
        t.start()
        t.join()
    assert errors and "thread" in errors[0]


def test_uow_does_not_expose_sqlalchemy_session_object_graph(session_factory):
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert not hasattr(uow, "session")
        assert not hasattr(uow, "engine")
        assert not hasattr(uow, "connection")


def test_repository_method_captured_in_owner_thread_rechecks_lifecycle(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        get_account = uow.accounts.get
        assert get_account(account_id).id == account_id
    with pytest.raises(RuntimeError, match="active transaction"):
        get_account(account_id)


def test_commit_and_rollback_end_the_only_transaction(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.accounts.update_cash(account_id, current_cash=9000)
        uow.commit()
        with pytest.raises(RuntimeError, match="active transaction"):
            uow.accounts.get(account_id)

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.accounts.update_cash(account_id, current_cash=1)
        uow.rollback()
        with pytest.raises(RuntimeError, match="active transaction"):
            uow.accounts.get(account_id)


def test_normal_exit_without_commit_explicitly_rolls_back(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        uow.accounts.update_cash(account_id, current_cash=1)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert float(uow.accounts.get(account_id).current_cash) == 10000.0


def test_repository_cannot_bypass_uow_cross_thread_guard(session_factory):
    errors = []
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        def query_from_other_thread():
            try:
                uow.accounts.list_active_ai_accounts()
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=query_from_other_thread)
        thread.start()
        thread.join()

    assert errors and "thread" in errors[0]


def test_uow_rejects_non_sync_session(session_factory):
    uow = SqlAlchemyUnitOfWork(lambda: object())
    with pytest.raises(TypeError, match="synchronous"):
        with uow:
            pass


def test_factory_yields_independent_units(session_factory):
    a = SqlAlchemyUnitOfWork(session_factory)
    b = SqlAlchemyUnitOfWork(session_factory)
    with a, b:
        assert a is not b
        assert a.accounts is not b.accounts
        assert not hasattr(a, "session")
        assert not hasattr(b, "session")


def test_uow_exit_returns_checked_out_connection_to_pool(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'uow-pool.db'}",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    baseline = engine.pool.checkedout()

    with SqlAlchemyUnitOfWork(factory) as uow:
        # Force checkout; constructing a Session alone is lazy.
        assert uow.users.get_by_username("missing") is None
        assert engine.pool.checkedout() == baseline + 1

    assert engine.pool.checkedout() == baseline
    engine.dispose()


def test_account_repository_mutations_are_uow_controlled(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert [a.id for a in uow.accounts.list_active_ai_accounts()] == [account_id]
        uow.accounts.set_active(account_id, active=False)
        assert uow.accounts.list_active_ai_accounts() == []
        assert uow.accounts.get(account_id).is_active == "false"
        uow.commit()
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.accounts.get(account_id).is_active == "false"


def test_row_lock_helpers_return_rows_on_sqlite(session_factory):
    # with_for_update degrades to a no-op on SQLite; the interface must
    # still return the row so MySQL-path code is portable.
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.accounts.get_for_update(account_id).id == account_id
        assert uow.orders.list_pending_for_update() == []


def test_decision_view_converts_bool_like_fields(session_factory):
    from database.models import AIDecisionLog

    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        decision = uow.decisions.add(
            AIDecisionLog(
                account_id=account_id,
                reason="test",
                operation="open",
                target_portion=0.1,
                total_balance=10000.0,
                executed="true",
            )
        )
        view = DecisionView.from_entity(decision)
        uow.commit()
    assert view.executed is True  # "true"/"false" string column -> bool
    assert view.operation == "open"
    assert view.account_id == account_id


def test_repositories_import_boundary():
    """Repositories must not import FastAPI, Agent, market providers or WS."""
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import benchmark.persistence\n"
        "import benchmark.persistence.sqlalchemy_repositories\n"
        "banned = ['fastapi', 'services.agent', 'services.market_data', 'api.ws']\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "assert not loaded, f'boundary violation: {loaded}'\n"
    ) % BACKEND_DIR
    subprocess.run([sys.executable, "-c", code], check=True)


# ---- startup migration registry (database/migrations_startup.py) ----


def test_sqlite_startup_migrations_add_missing_columns_idempotently():
    from database.migrations_startup import STARTUP_MIGRATIONS, run_startup_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE agent_period_checkpoints (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY)"))

    applied = run_startup_migrations(engine)
    assert applied == [
        "202608_scheduled_job_occurrences",
        "202608_trade_command_receipts",
        "202608_account_runtime_configs",
        "202606_agent_checkpoint_volatility",
        "202606_account_tool_routing_enabled",
    ]
    # second run: already applied, nothing to do, no error
    assert run_startup_migrations(engine) == []

    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(accounts)"))]
    assert "tool_routing_enabled" in cols
    # MySQL-only migrations must not run against sqlite
    assert all(m.dialect in ("sqlite", "mysql", None) for m in STARTUP_MIGRATIONS)


def test_occurrence_migration_upgrades_legacy_run_date_schema():
    from database.migrations_startup import (
        STARTUP_MIGRATIONS,
        run_startup_migrations,
    )

    migration = next(
        item
        for item in STARTUP_MIGRATIONS
        if item.migration_id == "202608_scheduled_job_occurrences_epoch_us"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scheduled_job_occurrences ("
                "id INTEGER PRIMARY KEY, job_id VARCHAR(255) NOT NULL, "
                "run_date DATETIME NOT NULL, consumed_at DATETIME NOT NULL, "
                "CONSTRAINT uix_scheduled_job_occurrence_key "
                "UNIQUE (job_id, run_date))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scheduled_job_occurrences "
                "(id, job_id, run_date, consumed_at) VALUES "
                "(1, 'once', '2026-08-13 01:02:03.123456', "
                "'2026-08-13 01:02:04')"
            )
        )

    assert run_startup_migrations(engine, [migration]) == [
        "202608_scheduled_job_occurrences_epoch_us"
    ]
    with engine.connect() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(scheduled_job_occurrences)"))
        }
        row = conn.execute(
            text(
                "SELECT job_id, run_at_epoch_us "
                "FROM scheduled_job_occurrences"
            )
        ).one()
    assert columns >= {"job_id", "run_at_epoch_us", "consumed_at"}
    assert "run_date" not in columns
    assert row == ("once", 1786582923123456)


def test_occurrence_migration_resumes_after_temp_table_creation():
    from database.migrations_startup import STARTUP_MIGRATIONS, run_startup_migrations

    migration = next(
        item
        for item in STARTUP_MIGRATIONS
        if item.migration_id == "202608_scheduled_job_occurrences_epoch_us"
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scheduled_job_occurrences ("
                "id INTEGER PRIMARY KEY, job_id VARCHAR(255) NOT NULL, "
                "run_date DATETIME NOT NULL, consumed_at DATETIME NOT NULL, "
                "UNIQUE (job_id, run_date))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scheduled_job_occurrences VALUES "
                "(1, 'once', '2026-08-13 01:02:03.123456', '2026-08-13 01:02:04')"
            )
        )
        # Simulate a prior process dying immediately after phase 1.
        conn.execute(
            text(
                "CREATE TABLE scheduled_job_occurrences_epoch_us ("
                "id INTEGER PRIMARY KEY, job_id VARCHAR(255) NOT NULL, "
                "run_at_epoch_us INTEGER NOT NULL, consumed_at DATETIME NOT NULL, "
                "UNIQUE (job_id, run_at_epoch_us))"
            )
        )

    assert run_startup_migrations(engine, [migration]) == [migration.migration_id]
    assert run_startup_migrations(engine, [migration]) == []
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM scheduled_job_occurrences")
        ).scalar_one() == 1
        temp_exists = conn.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='scheduled_job_occurrences_epoch_us'"
            )
        ).scalar_one()
    assert temp_exists == 0


def test_occurrence_migration_resumes_after_source_drop_before_rename():
    from database.migrations_startup import STARTUP_MIGRATIONS, run_startup_migrations

    occurrence_migrations = [
        item
        for item in STARTUP_MIGRATIONS
        if item.migration_id.startswith("202608_scheduled_job_occurrences")
    ]
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scheduled_job_occurrences_epoch_us ("
                "id INTEGER PRIMARY KEY, job_id VARCHAR(255) NOT NULL, "
                "run_at_epoch_us INTEGER NOT NULL, consumed_at DATETIME NOT NULL, "
                "UNIQUE (job_id, run_at_epoch_us))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scheduled_job_occurrences_epoch_us VALUES "
                "(1, 'once', 1786582923123456, '2026-08-13 01:02:04')"
            )
        )

    # Exercise the real registry order: the preceding create migration must
    # recognize the temp-only phase rather than creating an empty final table.
    assert run_startup_migrations(engine, occurrence_migrations) == [
        "202608_scheduled_job_occurrences_epoch_us"
    ]
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT job_id, run_at_epoch_us "
                "FROM scheduled_job_occurrences"
            )
        ).one() == ("once", 1786582923123456)


def test_occurrence_migration_merges_temp_rows_when_final_table_also_exists():
    from database.migrations_startup import STARTUP_MIGRATIONS, run_startup_migrations

    occurrence_migrations = [
        item
        for item in STARTUP_MIGRATIONS
        if item.migration_id.startswith("202608_scheduled_job_occurrences")
    ]
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        for table in (
            "scheduled_job_occurrences",
            "scheduled_job_occurrences_epoch_us",
        ):
            conn.execute(
                text(
                    f"CREATE TABLE {table} ("
                    "id INTEGER PRIMARY KEY, job_id VARCHAR(255) NOT NULL, "
                    "run_at_epoch_us INTEGER NOT NULL, consumed_at DATETIME NOT NULL, "
                    "UNIQUE (job_id, run_at_epoch_us))"
                )
            )
        conn.execute(
            text(
                "INSERT INTO scheduled_job_occurrences_epoch_us VALUES "
                "(1, 'recovered', 1786582923123456, '2026-08-13 01:02:04')"
            )
        )

    assert run_startup_migrations(engine, occurrence_migrations) == [
        "202608_scheduled_job_occurrences_epoch_us"
    ]
    with engine.connect() as conn:
        assert conn.execute(
            text(
                "SELECT job_id, run_at_epoch_us FROM scheduled_job_occurrences"
            )
        ).one() == ("recovered", 1786582923123456)
        assert conn.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='scheduled_job_occurrences_epoch_us'"
            )
        ).scalar_one() == 0


def test_occurrence_migration_serializes_concurrent_sqlite_startups(tmp_path):
    from database.migrations_startup import STARTUP_MIGRATIONS, run_startup_migrations

    migration = next(
        item
        for item in STARTUP_MIGRATIONS
        if item.migration_id == "202608_scheduled_job_occurrences_epoch_us"
    )
    url = f"sqlite:///{tmp_path / 'migration-race.db'}"
    setup_engine = create_engine(url, connect_args={"timeout": 5})
    with setup_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE scheduled_job_occurrences ("
                "id INTEGER PRIMARY KEY, job_id VARCHAR(255) NOT NULL, "
                "run_date DATETIME NOT NULL, consumed_at DATETIME NOT NULL, "
                "UNIQUE (job_id, run_date))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scheduled_job_occurrences VALUES "
                "(1, 'once', '2026-08-13 01:02:03.123456', '2026-08-13 01:02:04')"
            )
        )
    setup_engine.dispose()

    barrier = threading.Barrier(2)

    def migrate():
        engine = create_engine(url, connect_args={"timeout": 5})
        try:
            barrier.wait(2)
            return run_startup_migrations(engine, [migration])
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: migrate(), range(2)))

    assert sorted(len(result) for result in results) == [0, 1]
    verify_engine = create_engine(url)
    with verify_engine.connect() as conn:
        assert conn.execute(
            text("SELECT COUNT(*) FROM scheduled_job_occurrences")
        ).scalar_one() == 1
    verify_engine.dispose()


def test_mysql_startup_migration_holds_advisory_lock_across_check_and_apply():
    from database.migrations_startup import StartupMigration, run_startup_migrations

    events = []

    class ScalarResult:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def exec_driver_sql(self, statement):
            events.append(statement)
            return ScalarResult("bench_db")

        def execute(self, statement, params=None):
            sql = str(statement)
            events.append((sql, params))
            return ScalarResult(1)

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    class Engine:
        dialect = type("Dialect", (), {"name": "mysql"})()

        def connect(self):
            return Connection()

    migration = StartupMigration(
        "concurrent_mysql",
        "mysql",
        lambda conn: events.append("check") or False,
        lambda conn: events.append("apply"),
        fatal=True,
    )

    assert run_startup_migrations(Engine(), [migration]) == ["concurrent_mysql"]
    get_lock_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and "GET_LOCK" in event[0]
    )
    release_lock_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and "RELEASE_LOCK" in event[0]
    )
    assert get_lock_index < events.index("check") < events.index("apply")
    assert events.index("apply") < release_lock_index


def test_mysql_occurrence_migration_drops_only_exact_legacy_unique_key(monkeypatch):
    import sqlalchemy
    from database.migrations_startup import (
        _upgrade_scheduled_job_occurrences_to_epoch_us,
    )

    class Connection:
        dialect = type("Dialect", (), {"name": "mysql"})()

        def __init__(self):
            self.columns = {
                "id": False,
                "job_id": False,
                "run_date": False,
                "consumed_at": False,
            }
            self.constraints = [
                {"name": "legacy_occurrence", "column_names": ["job_id", "run_date"]},
                {"name": "business_guard", "column_names": ["job_id", "consumed_at"]},
            ]
            self.dropped = []

        def execute(self, statement, params=None):
            sql = str(statement)
            if "ADD COLUMN run_at_epoch_us" in sql:
                self.columns["run_at_epoch_us"] = True
            elif "DROP COLUMN run_date" in sql:
                self.columns.pop("run_date", None)
                self.columns["run_at_epoch_us"] = False
            elif "ADD CONSTRAINT uix_scheduled_job_occurrence_key" in sql:
                self.constraints.append(
                    {
                        "name": "uix_scheduled_job_occurrence_key",
                        "column_names": ["job_id", "run_at_epoch_us"],
                    }
                )

        def exec_driver_sql(self, statement):
            self.dropped.append(statement)
            name = statement.split("`")[1]
            self.constraints = [
                item for item in self.constraints if item["name"] != name
            ]

    class Inspector:
        def __init__(self, conn):
            self.conn = conn

        def has_table(self, name):
            return name == "scheduled_job_occurrences"

        def get_columns(self, name):
            return [
                {"name": column, "nullable": nullable}
                for column, nullable in self.conn.columns.items()
            ]

        def get_unique_constraints(self, name):
            return list(self.conn.constraints)

    monkeypatch.setattr(sqlalchemy, "inspect", lambda conn: Inspector(conn))
    conn = Connection()

    _upgrade_scheduled_job_occurrences_to_epoch_us(conn)

    assert conn.dropped == [
        "ALTER TABLE scheduled_job_occurrences DROP INDEX `legacy_occurrence`"
    ]
    assert any(item["name"] == "business_guard" for item in conn.constraints)
    assert any(
        item["column_names"] == ["job_id", "run_at_epoch_us"]
        for item in conn.constraints
    )


def test_fresh_schema_needs_no_sqlite_migrations():
    from database.migrations_startup import run_startup_migrations

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    assert run_startup_migrations(engine) == []


class _FakeMySQLConnection:
    """Answers information_schema column-type queries and records DDL."""

    def __init__(self, column_types):
        # {table: {column: data_type}}
        self.column_types = column_types
        self.executed_ddl = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def _scalar(value):
        class Result:
            def scalar_one(self):
                return value

            def scalar_one_or_none(self):
                return value

        return Result()

    def exec_driver_sql(self, statement):
        assert statement == "SELECT DATABASE()"
        return self._scalar("bench_test")

    def execute(self, statement, params=None):
        sql = str(statement)
        if "information_schema.COLUMNS" in sql:
            table = params["table_name"]
            return [
                (name, data_type)
                for name, data_type in self.column_types.get(table, {}).items()
            ]
        if "GET_LOCK" in sql or "RELEASE_LOCK" in sql:
            return self._scalar(1)
        self.executed_ddl.append(sql)
        return []

    def commit(self):
        pass

    def rollback(self):
        pass


class _FakeMySQLEngine:
    def __init__(self, conn):
        self._conn = conn
        from types import SimpleNamespace

        self.dialect = SimpleNamespace(name="mysql")

    def begin(self):
        conn = self._conn

        class _Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def connect(self):
        return self._conn


def _mysql_migrations():
    from database.migrations_startup import STARTUP_MIGRATIONS

    return [m for m in STARTUP_MIGRATIONS if m.dialect == "mysql"]


def test_mysql_migrations_skip_ddl_when_columns_already_widened():
    from database.migrations_startup import run_startup_migrations

    conn = _FakeMySQLConnection(
        {
            "ai_decision_logs": {"reason": "text"},
            "agent_traces": {
                "content": "longtext",
                "tool_calls": "longtext",
                "tool_output": "longtext",
            },
        }
    )
    engine = _FakeMySQLEngine(conn)

    applied = run_startup_migrations(engine, migrations=_mysql_migrations())

    assert applied == []
    assert conn.executed_ddl == [], "ALTER TABLE ran even though schema is current"


def test_mysql_migrations_apply_once_then_second_startup_is_a_noop():
    from database.migrations_startup import run_startup_migrations

    conn = _FakeMySQLConnection(
        {
            "ai_decision_logs": {"reason": "varchar"},
            "agent_traces": {
                "content": "text",
                "tool_calls": "text",
                "tool_output": "text",
            },
        }
    )
    engine = _FakeMySQLEngine(conn)

    applied = run_startup_migrations(engine, migrations=_mysql_migrations())
    assert applied == [
        "202606_ai_decision_reason_text",
        "202606_agent_traces_longtext",
    ]
    assert any("ALTER TABLE ai_decision_logs" in d for d in conn.executed_ddl)
    assert any("ALTER TABLE agent_traces" in d for d in conn.executed_ddl)

    # Simulate the widened schema after the first startup: the second
    # startup must not run any DDL again.
    conn.column_types["ai_decision_logs"]["reason"] = "text"
    conn.column_types["agent_traces"] = {
        "content": "longtext",
        "tool_calls": "longtext",
        "tool_output": "longtext",
    }
    conn.executed_ddl.clear()

    assert run_startup_migrations(engine, migrations=_mysql_migrations()) == []
    assert conn.executed_ddl == []
