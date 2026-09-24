from pathlib import Path

import pytest
from sqlalchemy import create_engine

from database.safety import validate_database_target


def test_protected_database_aliases(tmp_path):
    source = tmp_path / "original.sqlite"
    source.touch()
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(source)
    hardlink = tmp_path / "hard.sqlite"
    hardlink.hardlink_to(source)
    env = {"PROTECTED_DATABASE_PATH": str(source)}
    for path in (source, alias, hardlink):
        with pytest.raises(ValueError):
            validate_database_target(f"sqlite:///{path}", env)
    with pytest.raises(ValueError):
        validate_database_target(f"sqlite:///file://localhost{alias}?uri=true", env)
    with pytest.raises(ValueError):
        validate_database_target(f"sqlite:///file:{source}#ignored?uri=true", env)
    validate_database_target(f"sqlite:///{tmp_path / 'fresh.sqlite'}", env)
    assert source.stat().st_size == 0


def test_explicit_schema_and_migration_guard(tmp_path, monkeypatch):
    from benchmark.bootstrap.schema import run_schema_bootstrap
    from database.migrations_startup import run_startup_migrations

    source = tmp_path / "protected.sqlite"
    monkeypatch.setenv("PROTECTED_DATABASE_PATH", str(source))
    engine = create_engine(f"sqlite:///{source}")
    for operation in (run_schema_bootstrap, run_startup_migrations):
        with pytest.raises(ValueError):
            operation(engine)
    assert not source.exists()


def test_production_requires_dedicated_mysql():
    env = {"WAVE3_PRODUCTION": "true"}
    for url in ("sqlite://", "mysql+pymysql://localhost/alpha_arena"):
        with pytest.raises(ValueError):
            validate_database_target(url, env)
    validate_database_target("mysql+pymysql://localhost/alpha_arena_wave3", env)


def test_runtime_reset_updates_api(monkeypatch):
    from benchmark.extensions.host import get_extension_runtime, reset_extension_runtime
    from services.extension_config_service import get_extension_config_service

    reset_extension_runtime()
    first = get_extension_config_service().runtime
    assert first is get_extension_runtime()
    reset_extension_runtime()
    assert get_extension_config_service().runtime is get_extension_runtime()
    assert get_extension_runtime() is not first
    reset_extension_runtime()


def test_sandbox_cleanup_is_instance_scoped(monkeypatch):
    from services.container_service import ContainerService

    monkeypatch.setenv("SANDBOX_INSTANCE", "wave3")
    labels = ContainerService._container_labels(None)
    assert labels["open-alpha-arena-bench.instance"] == "wave3"
    source = (Path(__file__).parents[1] / "services/container_service.py").read_text()
    assert 'filters={"label": "open-alpha-arena-bench.managed=true"}' not in source
