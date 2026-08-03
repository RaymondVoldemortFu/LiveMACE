"""M18: bootstrap pipeline, task lifecycle and app factory acceptance tests."""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmark.bootstrap.runtime import (
    BootstrapContext,
    StartupMode,
    bootstrap_runtime_sync,
    shutdown_runtime_sync,
)
from benchmark.bootstrap.tasks import TaskDescriptor, TaskRegistry, TaskState


def test_import_has_no_runtime_side_effects():
    """Importing benchmark must not pull in engine/scheduler modules."""
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import benchmark.bootstrap.runtime, benchmark.bootstrap.tasks\n"
        "import benchmark.persistence\n"
        "banned = ['database.connection', 'services.startup', 'services.scheduler']\n"
        "loaded = [m for m in banned if m in sys.modules]\n"
        "assert not loaded, f'side-effect imports: {loaded}'\n"
    ) % BACKEND_DIR
    subprocess.run([sys.executable, "-c", code], check=True)


def _tracking_task(task_id, log, required=True, fail=False, dependencies=()):
    def start():
        if fail:
            raise RuntimeError(f"{task_id} start failed")
        log.append(("start", task_id))

    def stop():
        log.append(("stop", task_id))

    return TaskDescriptor(task_id, start, stop, required=required, dependencies=dependencies)


def test_duplicate_task_id_rejected():
    registry = TaskRegistry()
    registry.register(_tracking_task("a", []))
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(_tracking_task("a", []))


def test_start_all_is_idempotent_and_ordered():
    log = []
    registry = TaskRegistry()
    for task_id in ("a", "b", "c"):
        registry.register(_tracking_task(task_id, log))
    registry.start_all()
    registry.start_all()  # second call must not double-start
    assert log == [("start", "a"), ("start", "b"), ("start", "c")]
    assert set(registry.status().values()) == {TaskState.RUNNING}


def test_stop_all_reverse_order_and_reports_stop_failures():
    log = []
    registry = TaskRegistry()
    registry.register(_tracking_task("a", log))
    bad_stop = TaskDescriptor(
        "b",
        start=lambda: log.append(("start", "b")),
        stop=lambda: (_ for _ in ()).throw(RuntimeError("stop failed")),
    )
    registry.register(bad_stop)
    registry.register(_tracking_task("c", log))
    registry.start_all()

    results = registry.stop_all()
    stops = [entry for entry in log if entry[0] == "stop"]
    assert stops == [("stop", "c"), ("stop", "a")]  # reverse start order
    assert results["b"] == "stop failed"
    assert results["a"] is None and results["c"] is None


def test_required_failure_raises_and_marks_not_ready():
    registry = TaskRegistry()
    registry.register(_tracking_task("ok", []))
    registry.register(_tracking_task("boom", [], fail=True))
    registry.register(_tracking_task("never", []))
    with pytest.raises(RuntimeError, match="boom start failed"):
        registry.start_all()
    status = registry.status()
    assert status["boom"] == TaskState.FAILED
    assert status["never"] == TaskState.REGISTERED
    assert not registry.is_ready()
    assert registry.health()["boom"] == "required_failed"


def test_optional_failure_continues_and_health_degrades():
    log = []
    registry = TaskRegistry()
    registry.register(_tracking_task("sandbox", log, required=False, fail=True))
    registry.register(_tracking_task("after", log))
    registry.start_all()
    assert ("start", "after") in log
    assert registry.is_ready()
    assert registry.health()["sandbox"] == "degraded"


def test_dependency_not_running_skips_optional_task():
    log = []
    registry = TaskRegistry()
    registry.register(_tracking_task("dep", log, required=False, fail=True))
    registry.register(
        _tracking_task("dependent", log, required=False, dependencies=("dep",))
    )
    registry.start_all()
    assert registry.status()["dependent"] == TaskState.SKIPPED


def _stage_recorder(calls):
    return {
        "schema_stage": lambda: calls.append("schema") or "schema-report",
        "seed_stage": lambda: calls.append("seed") or "seed-report",
        "credential_stage": lambda: calls.append("credentials") or "cred-report",
    }


def test_schema_only_runs_schema_stage_only():
    calls = []
    handle = bootstrap_runtime_sync(
        BootstrapContext(mode=StartupMode.SCHEMA_ONLY, **_stage_recorder(calls))
    )
    assert calls == ["schema"]
    assert handle.registry.status() == {}


def test_no_background_runs_stages_but_no_tasks():
    calls = []
    handle = bootstrap_runtime_sync(
        BootstrapContext(mode=StartupMode.NO_BACKGROUND, **_stage_recorder(calls))
    )
    assert calls == ["schema", "seed", "credentials"]
    assert handle.registry.status() == {}


def test_full_starts_tasks_and_shutdown_stops_them():
    calls, log = [], []
    handle = bootstrap_runtime_sync(
        BootstrapContext(
            mode=StartupMode.FULL,
            task_descriptors=[_tracking_task("t1", log), _tracking_task("t2", log)],
            **_stage_recorder(calls),
        )
    )
    assert calls == ["schema", "seed", "credentials"]
    assert [e for e in log if e[0] == "start"] == [("start", "t1"), ("start", "t2")]
    shutdown_runtime_sync(handle)
    assert [e for e in log if e[0] == "stop"] == [("stop", "t2"), ("stop", "t1")]


def test_partial_start_failure_stops_started_tasks_only():
    calls, log = [], []
    with pytest.raises(RuntimeError, match="boom start failed"):
        bootstrap_runtime_sync(
            BootstrapContext(
                mode=StartupMode.FULL,
                task_descriptors=[
                    _tracking_task("t1", log),
                    _tracking_task("boom", log, fail=True),
                    _tracking_task("t3", log),
                ],
                **_stage_recorder(calls),
            )
        )
    # t1 started and was stopped; t3 never started so never stopped.
    assert ("stop", "t1") in log
    assert ("start", "t3") not in log and ("stop", "t3") not in log


def test_default_task_table_matches_startup_contract():
    from benchmark.bootstrap.tasks import default_task_descriptors

    descriptors = {d.task_id: d for d in default_task_descriptors()}
    expected_required = {
        "redis_tool_cache": True,
        "extension_catalog": True,
        "docker_sandbox": False,
        "scheduler": True,
        "market_tasks": True,
        "asset_curve_backfill_1h": False,
        "ai_auto_trading": True,
        "price_cache_cleanup": False,
        "margin_monitor": True,
        "order_scheduler": False,
        "eval_checkpoint_job": False,
    }
    assert {k: d.required for k, d in descriptors.items()} == expected_required
    # Catalog must load after Redis and before the scheduler starts jobs.
    order = list(descriptors)
    assert order.index("redis_tool_cache") < order.index("extension_catalog") < order.index("scheduler")
    assert descriptors["ai_auto_trading"].stop is not None


def test_auto_trading_setup_failure_propagates_instead_of_becoming_running(
    monkeypatch,
):
    from services import scheduler

    monkeypatch.setenv("AI_TRADE_FIRST_EXECUTION_TIME", "2026-08-02T08:00:00+08:00")
    monkeypatch.setattr(
        scheduler,
        "_ensure_market_data_ready",
        lambda: (_ for _ in ()).throw(RuntimeError("market unavailable")),
    )

    # In the bootstrap order the scheduler task is running before
    # ai_auto_trading starts; reproduce that precondition here.
    scheduler.task_scheduler.start()
    try:
        with pytest.raises(RuntimeError, match="market unavailable"):
            scheduler.reset_auto_trading_job()
    finally:
        assert scheduler.task_scheduler.shutdown()


@pytest.mark.slow
def test_app_import_and_no_background_acceptance(tmp_path):
    """M18 acceptance, in one subprocess (heavy service imports ~35s):

    - `import main` starts no scheduler and writes no DB;
    - a NO_BACKGROUND app serves routes with zero runtime tasks.
    """
    db_path = (tmp_path / "accept.db").as_posix()
    code = f"""
import os, sys
sys.path.insert(0, r'{BACKEND_DIR}')
os.environ['DATABASE_URL'] = 'sqlite:///{db_path}'
os.environ['ENABLE_ACCOUNT_CREATION_API'] = 'true'
os.environ.pop('ALPACA_KEY', None)
os.environ.pop('ALPACA_SECRET', None)

import main
from services.scheduler import task_scheduler
sched = task_scheduler.scheduler
assert not (sched and sched.running), 'import main started the scheduler'
assert not os.path.exists('{db_path}'), 'import main wrote the database'

from fastapi.testclient import TestClient
from benchmark.bootstrap import create_app, StartupMode
app = create_app(mode=StartupMode.NO_BACKGROUND)
with TestClient(app) as client:
    assert client.get('/api/health').status_code == 200
    handle = app.state.runtime_handle
    assert len(handle.registry.status()) == 0, 'NO_BACKGROUND registered runtime tasks'
    sched = task_scheduler.scheduler
    assert not (sched and sched.running), 'NO_BACKGROUND started the scheduler'

    # Business entry points must not implicitly start the scheduler.
    resp = client.post('/api/account/', json={{'name': 'nb-entry-test'}})
    assert resp.status_code == 200, resp.text
    sched = task_scheduler.scheduler
    assert not (sched and sched.running), 'account creation started the scheduler'

    from api.ws import manager
    fake_ws = object()
    manager.register(7, fake_ws)
    sched = task_scheduler.scheduler
    assert not (sched and sched.running), 'WS registration started the scheduler'
    manager.unregister(7, fake_ws)
assert os.path.exists('{db_path}'), 'schema bootstrap did not run'
print('ACCEPTANCE_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "ACCEPTANCE_OK" in result.stdout
