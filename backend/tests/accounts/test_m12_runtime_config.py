"""M12: account extension config — mapping, validation, service, migration tests."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmark.accounts import (
    AccountExtensionConfig,
    ConfigValidationError,
    config_from_legacy_account,
    get_runtime_config,
    save_runtime_config,
)
from benchmark.accounts.service import RuntimeConfigConflictError, RuntimeConfigRecord
from benchmark.accounts.validation import (
    VALIDATION_STATUS_INVALID,
    VALIDATION_STATUS_VALID,
    validate_extension_config,
)
from benchmark.persistence import AccountRuntimeConfigRepository, SqlAlchemyUnitOfWork
from database.connection import Base
from database.models import Account, User


class _LegacyAccount:
    """Minimal stand-in for a legacy accounts row."""

    def __init__(self, **flags):
        self.agent_type = flags.get("agent_type", "react")
        self.memory_enabled = flags.get("memory_enabled", "false")
        self.tool_routing_enabled = flags.get("tool_routing_enabled", "true")
        self.enable_rule_aware = flags.get("enable_rule_aware", "false")


# --------------------------------------------------------------------------- #
# DTO
# --------------------------------------------------------------------------- #


def test_dto_rejects_empty_agent_id():
    with pytest.raises(ConfigValidationError):
        AccountExtensionConfig(agent_id="")


def test_dto_is_immutable_and_normalizes_containers():
    source = {"nested": {"values": [1, 2]}}
    cfg = AccountExtensionConfig(
        agent_id="core.react",
        agent_config=source,
        toolset_ids=["a", "b"],
        component_versions={"core.react": "1.0.0"},
    )
    assert cfg.toolset_ids == ("a", "b")
    with pytest.raises(Exception):
        cfg.agent_id = "other"  # frozen dataclass
    with pytest.raises(TypeError):
        cfg.agent_config["nested"]["values"] = ()
    with pytest.raises(TypeError):
        cfg.component_versions["core.react"] = "9.9.9"
    source["nested"]["values"].append(3)
    assert cfg.to_dict()["agent_config"] == {"nested": {"values": [1, 2]}}


def test_dto_rejects_non_json_agent_config():
    with pytest.raises(ConfigValidationError):
        AccountExtensionConfig(
            agent_id="core.react",
            agent_config={"unsupported": {"a", "set"}},
        )


def test_dto_round_trips_through_dict():
    cfg = AccountExtensionConfig(
        agent_id="core.react",
        agent_config={"max_steps": 7},
        prompt_profile_id="core.react.default",
        component_versions={"core.react": "1.0.0"},
    )
    assert AccountExtensionConfig.from_dict(cfg.to_dict()) == cfg


def test_dto_carries_no_secret_fields():
    cfg = AccountExtensionConfig(agent_id="core.react")
    keys = set(cfg.to_dict())
    assert not keys & {"api_key", "model", "base_url"}


# --------------------------------------------------------------------------- #
# Legacy mapping
# --------------------------------------------------------------------------- #


def test_map_default_react_account():
    cfg = config_from_legacy_account(_LegacyAccount())
    assert cfg.agent_id == "core.react"
    assert cfg.prompt_profile_id == "core.react.tool-routing"  # routing on by default
    assert cfg.agent_config["tool_routing_enabled"] is True
    assert cfg.agent_config["memory_enabled"] is False


def test_map_react_prompt_profile_matrix():
    combos = {
        ("false", "false"): "core.react.default",
        ("true", "false"): "core.react.memory",
        ("false", "true"): "core.react.tool-routing",
        ("true", "true"): "core.react.memory-tool-routing",
    }
    for (mem, routing), profile in combos.items():
        cfg = config_from_legacy_account(
            _LegacyAccount(memory_enabled=mem, tool_routing_enabled=routing)
        )
        assert cfg.prompt_profile_id == profile


def test_rule_aware_takes_precedence_over_agent_type():
    cfg = config_from_legacy_account(
        _LegacyAccount(agent_type="multi_agent", enable_rule_aware="true")
    )
    assert cfg.agent_id == "core.rule-aware"
    assert cfg.prompt_profile_id == "core.rule-aware.default"


def test_map_multi_agent():
    cfg = config_from_legacy_account(_LegacyAccount(agent_type="multi_agent"))
    assert cfg.agent_id == "core.multi-agent"
    assert cfg.prompt_profile_id == "core.multi-agent.default"


def test_map_baseline_has_no_prompt_profile():
    cfg = config_from_legacy_account(_LegacyAccount(agent_type="buy_hold"))
    assert cfg.agent_id == "baseline.buy-hold"
    assert cfg.prompt_profile_id is None
    assert cfg.is_baseline


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_validate_react_config_pins_version():
    cfg = config_from_legacy_account(_LegacyAccount())
    result = validate_extension_config(cfg)
    assert result.status == VALIDATION_STATUS_VALID
    assert result.resolved_config.component_versions["core.react"] == "1.0.0"
    # schema defaults applied to the agent config copy
    assert "max_steps" in result.resolved_config.agent_config


def test_validate_unknown_agent_is_configuration_invalid():
    cfg = AccountExtensionConfig(agent_id="core.does-not-exist")
    result = validate_extension_config(cfg)
    assert result.status == VALIDATION_STATUS_INVALID
    assert result.resolved_config is None
    assert any(issue.path == "agent_id" for issue in result.issues)


def test_validate_unknown_prompt_profile_is_invalid():
    cfg = AccountExtensionConfig(
        agent_id="core.react",
        prompt_profile_id="core.react.nope",
    )
    result = validate_extension_config(cfg)
    assert result.status == VALIDATION_STATUS_INVALID
    assert any(issue.path == "prompt_profile_id" for issue in result.issues)


def test_validate_bad_agent_config_schema_is_invalid():
    cfg = AccountExtensionConfig(
        agent_id="core.react",
        agent_config={"max_steps": -3},  # violates minimum: 1
    )
    result = validate_extension_config(cfg)
    assert result.status == VALIDATION_STATUS_INVALID
    assert any(issue.path.startswith("agent_config") for issue in result.issues)


def test_validate_baseline_rejects_stray_profile():
    cfg = AccountExtensionConfig(
        agent_id="baseline.grid",
        prompt_profile_id="core.react.default",
    )
    result = validate_extension_config(cfg)
    assert result.status == VALIDATION_STATUS_INVALID


# --------------------------------------------------------------------------- #
# Service + persistence
# --------------------------------------------------------------------------- #


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


def test_uow_exposes_runtime_config_repo(session_factory):
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert isinstance(
            uow.account_runtime_configs, AccountRuntimeConfigRepository
        )


def test_get_returns_none_when_unset(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert get_runtime_config(uow, account_id) is None


def test_save_and_read_round_trip(session_factory):
    account_id = _seed_account(session_factory)
    cfg = config_from_legacy_account(_LegacyAccount())
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        result = save_runtime_config(uow, account_id, cfg)
        assert result.valid
        assert result.updated_at.tzinfo is not None
        assert result.updated_at.utcoffset().total_seconds() == 0
        uow.commit()
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        stored = get_runtime_config(uow, account_id)
        assert stored.config.agent_id == "core.react"
        assert stored.config.component_versions["core.react"] == "1.0.0"
        assert stored.updated_at == result.updated_at


def test_runtime_config_api_schema_requires_aware_tokens():
    from pydantic import ValidationError

    from schemas.account import AccountRuntimeConfigSave

    payload = {"config": {"agent_id": "core.react"}}
    with pytest.raises(ValidationError):
        AccountRuntimeConfigSave(
            **payload,
            expected_updated_at=datetime(2026, 8, 30, 12, 0, 0),
        )

    parsed = AccountRuntimeConfigSave(
        **payload,
        expected_updated_at=datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert parsed.expected_updated_at.tzinfo is not None


def test_runtime_config_record_rejects_naive_token():
    with pytest.raises(ValueError, match="timezone-aware"):
        RuntimeConfigRecord(
            config=AccountExtensionConfig(agent_id="core.react"),
            status=VALIDATION_STATUS_VALID,
            updated_at=datetime(2026, 8, 30, 12, 0, 0),
        )


def test_save_invalid_config_marks_configuration_invalid(session_factory):
    account_id = _seed_account(session_factory)
    cfg = AccountExtensionConfig(agent_id="core.ghost")
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        result = save_runtime_config(uow, account_id, cfg)
        assert not result.valid
        assert result.status == VALIDATION_STATUS_INVALID
        uow.commit()
    # Row persisted but flagged; runtime must refuse rather than fall back.
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        row = uow.account_runtime_configs.get(account_id)
        assert row.validation_status == VALIDATION_STATUS_INVALID
        assert row.validation_errors_json


def test_optimistic_concurrency_conflict(session_factory):
    account_id = _seed_account(session_factory)
    cfg = config_from_legacy_account(_LegacyAccount())
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        save_runtime_config(uow, account_id, cfg)
        uow.commit()
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        current = get_runtime_config(uow, account_id)
        stale = current.updated_at
        # First writer succeeds and bumps the row.
        updated = save_runtime_config(
            uow,
            account_id,
            AccountExtensionConfig(agent_id="core.multi-agent"),
            expected_updated_at=stale,
        )
        assert updated.updated_at != stale
        uow.commit()
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        # A second writer using the stale token is rejected.
        with pytest.raises(RuntimeConfigConflictError):
            save_runtime_config(
                uow,
                account_id,
                AccountExtensionConfig(agent_id="core.react"),
                expected_updated_at=stale,
            )


def test_update_without_expected_token_conflicts(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        created = save_runtime_config(
            uow, account_id, AccountExtensionConfig(agent_id="core.react")
        )
        uow.commit()

    with SqlAlchemyUnitOfWork(session_factory) as uow:
        with pytest.raises(RuntimeConfigConflictError) as exc_info:
            save_runtime_config(
                uow,
                account_id,
                AccountExtensionConfig(agent_id="core.multi-agent"),
            )
        assert exc_info.value.expected_updated_at is None
        assert exc_info.value.actual_updated_at == created.updated_at


def test_create_with_unexpected_token_conflicts(session_factory):
    account_id = _seed_account(session_factory)
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        with pytest.raises(RuntimeConfigConflictError):
            save_runtime_config(
                uow,
                account_id,
                AccountExtensionConfig(agent_id="core.react"),
                expected_updated_at="2020-01-01T00:00:00",
            )


# --------------------------------------------------------------------------- #
# Startup migration (create table + idempotent backfill)
# --------------------------------------------------------------------------- #


def test_migration_creates_table_and_backfills_idempotently():
    from database.migrations_startup import (
        _account_runtime_configs_backfilled,
        run_startup_migrations,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    # Drop the config table so the migration must create it (create_all made it).
    from database.models import AccountRuntimeConfig

    AccountRuntimeConfig.__table__.drop(bind=engine)

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SqlAlchemyUnitOfWork(factory) as uow:
        user = User(username="u1", is_active="true")
        uow.users.add(user)
        for name, flags in (
            ("react", {}),
            ("rule", {"enable_rule_aware": "true"}),
            ("baseline", {"agent_type": "buy_hold"}),
        ):
            uow.accounts.add(
                Account(
                    user_id=user.id,
                    version="v1",
                    name=name,
                    account_type="AI",
                    initial_capital=10000.0,
                    current_cash=10000.0,
                    frozen_cash=0.0,
                    is_active="true",
                    **flags,
                )
            )
        uow.commit()

    run_startup_migrations(engine)

    with factory() as session:
        from database.models import AccountRuntimeConfig as ARC

        rows = session.query(ARC).order_by(ARC.account_id).all()
        assert len(rows) == 3
        agent_ids = {row.agent_id for row in rows}
        assert agent_ids == {"core.react", "core.rule-aware", "baseline.buy-hold"}

    # Idempotent: the guard now reports applied, and re-running inserts nothing.
    with engine.connect() as conn:
        assert _account_runtime_configs_backfilled(conn) is True

    run_startup_migrations(engine)
    with factory() as session:
        from database.models import AccountRuntimeConfig as ARC

        assert session.query(ARC).count() == 3


def test_sqlite_old_account_schema_backfills_on_first_startup():
    from database.migrations_startup import run_startup_migrations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE agent_period_checkpoints (id INTEGER PRIMARY KEY)")
        )
        conn.execute(
            text(
                "CREATE TABLE accounts ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, version VARCHAR(100), "
                "name VARCHAR(100), account_type VARCHAR(20), agent_type VARCHAR(20), "
                "memory_enabled VARCHAR(10), enable_rule_aware VARCHAR(10), "
                "is_active VARCHAR(10), initial_capital FLOAT, current_cash FLOAT, "
                "frozen_cash FLOAT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO accounts VALUES "
                "(1, 1, 'v1', 'old', 'AI', 'react', 'false', 'false', "
                "'true', 10000, 10000, 0)"
            )
        )

    applied = run_startup_migrations(engine)

    assert "202606_account_tool_routing_enabled" in applied
    assert "202608_account_runtime_configs_backfill" in applied
    assert applied.index("202606_account_tool_routing_enabled") < applied.index(
        "202608_account_runtime_configs_backfill"
    )
    assert "tool_routing_enabled" in {
        column["name"] for column in inspect(engine).get_columns("accounts")
    }
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT agent_id, agent_config_json, prompt_profile_id "
                "FROM account_runtime_configs WHERE account_id = 1"
            )
        ).one()
    assert row.agent_id == "core.react"
    assert row.prompt_profile_id == "core.react.tool-routing"
    agent_config = json.loads(row.agent_config_json)
    assert agent_config["memory_enabled"] is False
    assert agent_config["tool_routing_enabled"] is True


def test_backfill_guard_treats_partial_legacy_source_as_unavailable():
    from database.migrations_startup import (
        _account_runtime_configs_backfilled,
        _create_account_runtime_configs,
    )

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE accounts ("
                "id INTEGER PRIMARY KEY, account_type VARCHAR(20), "
                "agent_type VARCHAR(20))"
            )
        )
        conn.execute(text("INSERT INTO accounts VALUES (1, 'AI', 'react')"))
        _create_account_runtime_configs(conn)
        assert _account_runtime_configs_backfilled(conn) is True


def test_runtime_config_mysql_ddl_preserves_microsecond_lock_tokens():
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    from database.models import AccountRuntimeConfig

    ddl = str(
        CreateTable(AccountRuntimeConfig.__table__).compile(dialect=mysql.dialect())
    )
    assert "updated_at TIMESTAMP(6) NOT NULL" in ddl
