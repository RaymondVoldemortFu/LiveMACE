"""M19: UnitOfWork, repository, views and startup-migration contract tests."""

import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

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
        uow.session.add(user)
        uow.session.flush()
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
        uow.session.add(account)
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
        uow.commit()
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


def test_session_escape_hatch_cannot_bypass_cross_thread_guard(session_factory):
    """A session proxy obtained on the owner thread must reject use from
    any other thread on every operation, not only at property access."""
    errors = []
    results = []
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        session_proxy = uow.session  # obtained on the owner thread

        def use_from_other_thread():
            try:
                results.append(session_proxy.execute(text("SELECT 1")).scalar())
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=use_from_other_thread)
        thread.start()
        thread.join()

        # Same-thread use keeps working for legacy call sites.
        assert session_proxy.execute(text("SELECT 1")).scalar() == 1

    assert results == [], "bare session escaped the cross-thread guard"
    assert errors and "thread" in errors[0]


def test_captured_bound_method_cannot_bypass_cross_thread_guard(session_factory):
    """Capturing a callable off the proxy on the owner thread must not
    yield a bare Session bound method: the owner-thread check re-runs at
    call time (code-review P2 follow-up)."""
    errors = []
    results = []
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        escaped_execute = uow.session.execute  # captured on the owner thread

        def call_from_other_thread():
            try:
                results.append(escaped_execute(text("SELECT 1")).scalar())
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=call_from_other_thread)
        thread.start()
        thread.join()

        # The captured callable still works on the owner thread.
        assert escaped_execute(text("SELECT 1")).scalar() == 1

    assert results == [], "captured bound method escaped the cross-thread guard"
    assert errors and "thread" in errors[0]

    # After the UoW context closed, the captured callable must fail too.
    with pytest.raises(RuntimeError):
        escaped_execute(text("SELECT 1"))


def test_session_connection_cannot_bypass_cross_thread_guard(session_factory):
    """Connection/Engine objects returned by the session proxy must stay guarded."""
    errors = []
    results = []
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        connection = uow.session.connection()
        bind = uow.session.get_bind()

        def use_connection_from_other_thread():
            try:
                results.append(connection.execute(text("SELECT 1")).scalar())
            except RuntimeError as exc:
                errors.append(str(exc))

        def use_bind_from_other_thread():
            try:
                with bind.connect() as conn:
                    results.append(conn.execute(text("SELECT 1")).scalar())
            except RuntimeError as exc:
                errors.append(str(exc))

        for target in (use_connection_from_other_thread, use_bind_from_other_thread):
            thread = threading.Thread(target=target)
            thread.start()
            thread.join()

        assert connection.execute(text("SELECT 1")).scalar() == 1

    assert results == [], "session-returned DB object escaped the cross-thread guard"
    assert len(errors) == 2 and all("thread" in item for item in errors)


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
        assert a.session is not b.session


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
        "202608_trade_command_receipts",
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

    def execute(self, statement, params=None):
        sql = str(statement)
        if "information_schema.COLUMNS" in sql:
            table = params["table_name"]
            return [
                (name, data_type)
                for name, data_type in self.column_types.get(table, {}).items()
            ]
        self.executed_ddl.append(sql)
        return []


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
