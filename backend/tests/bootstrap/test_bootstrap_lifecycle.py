"""M18: bootstrap pipeline, task lifecycle and app factory acceptance tests."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmark.bootstrap.runtime import (
    BootstrapContext,
    RuntimeBootstrapError,
    RuntimeShutdownError,
    RuntimeHandle,
    StartupMode,
    bootstrap_runtime_sync,
    shutdown_runtime_sync,
)
from benchmark.bootstrap.tasks import (
    TaskDescriptor,
    TaskRegistry,
    TaskState,
    default_task_descriptors,
)


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

    return TaskDescriptor(
        task_id,
        start,
        stop,
        required=required,
        dependencies=dependencies,
        owns_resources=lambda: False,
    )


def test_duplicate_task_id_rejected():
    registry = TaskRegistry()
    registry.register(_tracking_task("a", []))
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(_tracking_task("a", []))


def test_resource_ownership_probe_requires_stop_callback():
    registry = TaskRegistry()

    with pytest.raises(ValueError, match="has no stop callback"):
        registry.register(
            TaskDescriptor(
                "unmanaged_resource",
                lambda: None,
                owns_resources=lambda: True,
            )
        )


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
    assert registry.status()["b"] == TaskState.STOP_FAILED


def test_stop_failure_is_retried_until_shutdown_succeeds():
    calls = []

    def stop():
        calls.append("stop")
        if len(calls) == 1:
            raise RuntimeError("not drained")

    registry = TaskRegistry()
    registry.register(TaskDescriptor("scheduler", start=lambda: None, stop=stop))
    registry.start_all()

    first = registry.stop_all()
    assert first == {"scheduler": "not drained"}
    assert registry.status()["scheduler"] == TaskState.STOP_FAILED
    assert registry.health()["scheduler"] == "stop_failed"
    assert not registry.is_ready()

    second = registry.stop_all()
    assert second == {"scheduler": None}
    assert calls == ["stop", "stop"]
    assert registry.status()["scheduler"] == TaskState.STOPPED


def test_start_preflight_rejects_stop_failed_state_without_partial_restart():
    starts = []
    registry = TaskRegistry()
    registry.register(
        TaskDescriptor(
            "first",
            start=lambda: starts.append("first"),
            stop=lambda: None,
        )
    )
    registry.register(
        TaskDescriptor(
            "second",
            start=lambda: starts.append("second"),
            stop=lambda: (_ for _ in ()).throw(RuntimeError("not drained")),
        )
    )
    registry.start_all()
    assert registry.stop_all()["second"] == "not drained"
    starts.clear()

    with pytest.raises(RuntimeError, match="not completed shutdown"):
        registry.start_all()

    assert starts == []
    assert registry.status() == {
        "first": TaskState.STOPPED,
        "second": TaskState.STOP_FAILED,
    }


def test_runtime_shutdown_retries_a_previous_stop_failure():
    calls = []

    def stop():
        calls.append("stop")
        if len(calls) == 1:
            raise RuntimeError("not drained")

    handle = bootstrap_runtime_sync(
        BootstrapContext(
            mode=StartupMode.FULL,
            task_descriptors=[TaskDescriptor("scheduler", lambda: None, stop)],
            **_stage_recorder([]),
        )
    )
    with pytest.raises(RuntimeShutdownError) as caught:
        shutdown_runtime_sync(handle)
    assert caught.value.failures == {"scheduler": "not drained"}

    shutdown_runtime_sync(handle)
    assert calls == ["stop", "stop"]
    assert handle.registry.status()["scheduler"] == TaskState.STOPPED


def test_stop_failure_preserves_dependency_until_dependent_retry_succeeds():
    log = []
    dependent_attempts = []

    def stop_dependent():
        dependent_attempts.append(1)
        log.append("stop:dependent")
        if len(dependent_attempts) == 1:
            raise RuntimeError("dependent busy")

    registry = TaskRegistry()
    registry.register(
        TaskDescriptor(
            "scheduler",
            start=lambda: None,
            stop=lambda: log.append("stop:scheduler"),
        )
    )
    registry.register(
        TaskDescriptor(
            "market_tasks",
            start=lambda: None,
            stop=lambda: log.append("stop:market_tasks"),
            dependencies=("scheduler",),
        )
    )
    registry.register(
        TaskDescriptor(
            "ai_auto_trading",
            start=lambda: None,
            stop=stop_dependent,
            dependencies=("scheduler", "market_tasks"),
        )
    )
    registry.start_all()

    handle = RuntimeHandle(StartupMode.FULL, registry)
    with pytest.raises(RuntimeShutdownError) as caught:
        shutdown_runtime_sync(handle)
    assert caught.value.failures["ai_auto_trading"] == "dependent busy"
    assert caught.value.failures["market_tasks"].startswith("stop deferred")
    assert caught.value.failures["scheduler"].startswith("stop deferred")
    assert log == ["stop:dependent"]
    assert registry.status() == {
        "scheduler": TaskState.RUNNING,
        "market_tasks": TaskState.RUNNING,
        "ai_auto_trading": TaskState.STOP_FAILED,
    }

    shutdown_runtime_sync(handle)
    assert log == [
        "stop:dependent",
        "stop:dependent",
        "stop:market_tasks",
        "stop:scheduler",
    ]
    assert set(registry.status().values()) == {TaskState.STOPPED}


def test_required_failure_raises_and_marks_not_ready():
    registry = TaskRegistry()
    registry.register(_tracking_task("ok", []))
    registry.register(_tracking_task("boom", [], fail=True))
    registry.register(_tracking_task("never", []))
    with pytest.raises(RuntimeError, match="boom start failed"):
        registry.start_all()
    status = registry.status()
    assert status["boom"] == TaskState.START_FAILED
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


def test_scheduler_stop_failure_propagates_through_runtime_shutdown(monkeypatch):
    from services import scheduler

    class FailingScheduler:
        def shutdown(self):
            return False

    monkeypatch.setattr(scheduler, "task_scheduler", FailingScheduler())
    handle = bootstrap_runtime_sync(
        BootstrapContext(
            mode=StartupMode.FULL,
            task_descriptors=[
                TaskDescriptor("scheduler", start=lambda: None, stop=scheduler.stop_scheduler)
            ],
            **_stage_recorder([]),
        )
    )

    with pytest.raises(RuntimeShutdownError) as caught:
        shutdown_runtime_sync(handle)

    assert caught.value.failures == {
        "scheduler": (
            "scheduler shutdown incomplete: running jobs did not stop within "
            "the shutdown timeout"
        )
    }


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


def test_start_failure_with_owned_resources_is_cleanup_retryable():
    owned = True
    stop_calls = []

    def start():
        raise RuntimeError("partial start")

    def stop():
        nonlocal owned
        stop_calls.append(1)
        owned = False

    registry = TaskRegistry()
    registry.register(
        TaskDescriptor(
            "partial",
            start,
            stop,
            required=False,
            owns_resources=lambda: owned,
        )
    )
    with pytest.raises(RuntimeError, match="partial start"):
        registry.start_all()
    assert registry.status()["partial"] == TaskState.START_CLEANUP_FAILED
    with pytest.raises(RuntimeError, match="not completed shutdown"):
        registry.start_all()
    assert registry.stop_all() == {"partial": None}
    assert stop_calls == [1]
    assert registry.status()["partial"] == TaskState.STOPPED


def test_bootstrap_cleanup_failure_returns_retryable_runtime_handle():
    stop_attempts = []
    owned = True

    def start_partial():
        raise RuntimeError("partial start")

    def stop_partial():
        nonlocal owned
        stop_attempts.append(1)
        if len(stop_attempts) == 1:
            raise RuntimeError("cleanup busy")
        owned = False

    with pytest.raises(RuntimeBootstrapError) as caught:
        bootstrap_runtime_sync(
            BootstrapContext(
                mode=StartupMode.FULL,
                task_descriptors=[
                    TaskDescriptor("dependency", lambda: None, lambda: None),
                    TaskDescriptor(
                        "partial",
                        start_partial,
                        stop_partial,
                        dependencies=("dependency",),
                        owns_resources=lambda: owned,
                    ),
                ],
                **_stage_recorder([]),
            )
        )

    assert caught.value.cleanup_failures["partial"] == "cleanup busy"
    assert caught.value.cleanup_failures["dependency"].startswith("stop deferred")
    assert caught.value.handle.registry.status() == {
        "dependency": TaskState.RUNNING,
        "partial": TaskState.STOP_FAILED,
    }
    shutdown_runtime_sync(caught.value.handle)
    assert stop_attempts == [1, 1]


def test_ownership_probe_failure_is_conservatively_cleanup_pending():
    stops = []
    registry = TaskRegistry()
    registry.register(
        TaskDescriptor(
            "partial",
            lambda: (_ for _ in ()).throw(RuntimeError("start failed")),
            lambda: stops.append(1),
            required=False,
            owns_resources=lambda: (_ for _ in ()).throw(RuntimeError("probe failed")),
        )
    )

    with pytest.raises(RuntimeError, match="start failed"):
        registry.start_all()
    assert registry.status()["partial"] == TaskState.START_CLEANUP_FAILED
    assert registry.stop_all() == {"partial": None}
    assert stops == [1]


def test_asgi_startup_cleanup_has_deadline_and_propagates_state(monkeypatch):
    import anyio
    from benchmark.bootstrap import app as app_module
    from benchmark.bootstrap.app import AppSettings, create_app

    handle = RuntimeHandle(StartupMode.FULL, TaskRegistry())
    start_error = RuntimeError("start boom")
    bootstrap_error = RuntimeBootstrapError(
        start_error,
        handle,
        {"scheduler": "cleanup busy"},
    )
    attempts = []

    async def fail_bootstrap(context):
        raise bootstrap_error

    async def fail_cleanup(runtime_handle, **kwargs):
        attempts.append(1)
        raise RuntimeShutdownError({"scheduler": "still busy"})

    monkeypatch.setattr(app_module, "bootstrap_runtime", fail_bootstrap)
    monkeypatch.setattr(app_module, "shutdown_runtime", fail_cleanup)
    app = create_app(
        settings=AppSettings(startup_cleanup_timeout_seconds=0.12),
        mode=StartupMode.FULL,
    )

    async def enter_lifespan():
        async with app.router.lifespan_context(app):
            pass

    started = time.monotonic()
    with pytest.raises(RuntimeBootstrapError) as caught:
        anyio.run(enter_lifespan)
    assert time.monotonic() - started < 0.5
    assert len(attempts) >= 2
    assert caught.value.start_error is start_error
    assert caught.value.cleanup_failures == {
        "scheduler": "still busy",
        "runtime": "startup cleanup deadline exceeded",
    }


def test_asgi_startup_cleanup_waits_for_inflight_worker(monkeypatch):
    import threading

    import anyio
    from benchmark.bootstrap import app as app_module
    from benchmark.bootstrap.app import AppSettings, create_app

    handle = RuntimeHandle(StartupMode.FULL, TaskRegistry())
    start_error = RuntimeError("start boom")
    bootstrap_error = RuntimeBootstrapError(
        start_error,
        handle,
        {"scheduler": "cleanup busy"},
    )
    cleanup_finished = threading.Event()
    observed_kwargs = []

    async def fail_bootstrap(context):
        raise bootstrap_error

    async def slow_successful_cleanup(runtime_handle, **kwargs):
        observed_kwargs.append(kwargs)
        await anyio.sleep(0.2)
        cleanup_finished.set()

    monkeypatch.setattr(app_module, "bootstrap_runtime", fail_bootstrap)
    monkeypatch.setattr(app_module, "shutdown_runtime", slow_successful_cleanup)
    app = create_app(
        settings=AppSettings(startup_cleanup_timeout_seconds=0.05),
        mode=StartupMode.FULL,
    )

    async def enter_lifespan():
        async with app.router.lifespan_context(app):
            pass

    started = time.monotonic()
    with pytest.raises(RuntimeError) as caught:
        anyio.run(enter_lifespan)
    elapsed = time.monotonic() - started
    assert caught.value is start_error
    assert elapsed >= 0.2
    assert cleanup_finished.is_set()
    assert observed_kwargs == [{}]


def test_asgi_startup_cleanup_deadline_does_not_abandon_failed_worker(monkeypatch):
    import threading

    import anyio
    from benchmark.bootstrap import app as app_module
    from benchmark.bootstrap.app import AppSettings, create_app

    handle = RuntimeHandle(StartupMode.FULL, TaskRegistry())
    start_error = RuntimeError("start boom")
    bootstrap_error = RuntimeBootstrapError(
        start_error,
        handle,
        {"scheduler": "cleanup busy"},
    )
    cleanup_finished = threading.Event()

    async def fail_bootstrap(context):
        raise bootstrap_error

    async def slow_failed_cleanup(runtime_handle, **kwargs):
        await anyio.sleep(0.2)
        cleanup_finished.set()
        raise RuntimeShutdownError({"scheduler": "still busy"})

    monkeypatch.setattr(app_module, "bootstrap_runtime", fail_bootstrap)
    monkeypatch.setattr(app_module, "shutdown_runtime", slow_failed_cleanup)
    app = create_app(
        settings=AppSettings(startup_cleanup_timeout_seconds=0.05),
        mode=StartupMode.FULL,
    )

    async def enter_lifespan():
        async with app.router.lifespan_context(app):
            pass

    started = time.monotonic()
    with pytest.raises(RuntimeBootstrapError) as caught:
        anyio.run(enter_lifespan)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.2
    assert cleanup_finished.is_set()
    assert caught.value.start_error is start_error
    assert caught.value.cleanup_failures == {
        "scheduler": "still busy",
        "runtime": "startup cleanup deadline exceeded",
    }


def test_shutdown_runtime_joins_worker_when_await_is_cancelled():
    import anyio
    from benchmark.bootstrap.runtime import shutdown_runtime

    finished = threading.Event()
    registry = TaskRegistry()

    def stop():
        time.sleep(0.2)
        finished.set()

    registry.register(TaskDescriptor("worker", lambda: None, stop))
    registry.start_all()
    handle = RuntimeHandle(StartupMode.FULL, registry)

    async def exercise():
        with anyio.move_on_after(0.05):
            await shutdown_runtime(handle)

    started = time.monotonic()
    anyio.run(exercise)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.2
    assert finished.is_set()
    assert registry.status()["worker"] == TaskState.STOPPED


def test_quiesced_auto_trading_stop_clears_family_when_sibling_fails(monkeypatch):
    from apscheduler.triggers.interval import IntervalTrigger

    from services import scheduler as scheduler_module
    from services.scheduler import JobSpec, TaskScheduler

    ts = TaskScheduler()
    ts.start()
    monkeypatch.setattr(scheduler_module, "task_scheduler", ts)

    def start_auto_trading():
        ts.reconcile_jobs(
            "auto_trading",
            (JobSpec("ai_trade_job", lambda: None, IntervalTrigger(seconds=60)),),
        )

    registry = TaskRegistry()
    registry.register(
        TaskDescriptor(
            "scheduler",
            start=lambda: None,
            stop=lambda: scheduler_module.stop_scheduler(),
            quiesce=scheduler_module.quiesce_scheduler,
        )
    )
    registry.register(
        TaskDescriptor(
            "ai_auto_trading",
            start_auto_trading,
            scheduler_module.stop_auto_trading_jobs,
            dependencies=("scheduler",),
        )
    )
    registry.register(
        TaskDescriptor(
            "other_dependent",
            start=lambda: None,
            stop=lambda: (_ for _ in ()).throw(RuntimeError("other still draining")),
            dependencies=("scheduler",),
        )
    )
    try:
        registry.start_all()
        results = registry.stop_all()
        assert results["other_dependent"] == "other still draining"
        assert results["ai_auto_trading"] is None
        assert "active dependents" in results["scheduler"]
        assert registry.status() == {
            "scheduler": TaskState.RUNNING,
            "ai_auto_trading": TaskState.STOPPED,
            "other_dependent": TaskState.STOP_FAILED,
        }
        assert "ai_trade_job" not in ts._registrations
        assert ts.scheduler.get_job("ai_trade_job") is None
        assert ts.is_running() is False
        assert ts.is_control_plane_running() is True
    finally:
        ts.shutdown(timeout=2)



def test_order_scheduler_partial_start_ownership_and_join_failure(monkeypatch):
    from services import order_scheduler as module

    class HalfStartedThread:
        def __init__(self, *args, **kwargs):
            self.alive = False

        def start(self):
            self.alive = True
            raise RuntimeError("thread start failed late")

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(module.threading, "Thread", HalfStartedThread)
    instance = module.OrderScheduler()
    with pytest.raises(RuntimeError, match="thread start failed late"):
        instance.start()
    assert instance.has_pending_cleanup() is True
    with pytest.raises(RuntimeError, match="did not stop"):
        instance.stop()

    descriptor = next(
        item
        for item in default_task_descriptors()
        if item.task_id == "order_scheduler"
    )
    assert descriptor.owns_resources is not None


def test_order_scheduler_concurrent_start_owns_exactly_one_thread(monkeypatch):
    from services import order_scheduler as module

    instance = module.OrderScheduler()
    worker_started = threading.Event()
    worker_count = 0
    worker_count_lock = threading.Lock()

    def run_worker():
        nonlocal worker_count
        with worker_count_lock:
            worker_count += 1
        worker_started.set()
        instance._stop_event.wait()

    monkeypatch.setattr(instance, "_run_scheduler", run_worker)
    callers = [threading.Thread(target=instance.start) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=1)

    assert worker_started.wait(timeout=1)
    assert worker_count == 1
    assert instance.has_pending_cleanup() is True
    instance.stop()
    assert instance.has_pending_cleanup() is False


def test_optional_partial_start_with_resources_aborts_and_is_not_ready():
    registry = TaskRegistry()
    registry.register(
        TaskDescriptor(
            "optional_partial",
            lambda: (_ for _ in ()).throw(RuntimeError("partial")),
            lambda: None,
            required=False,
            owns_resources=lambda: True,
        )
    )

    with pytest.raises(RuntimeError, match="partial"):
        registry.start_all()
    assert registry.status()["optional_partial"] == TaskState.START_CLEANUP_FAILED
    assert registry.is_ready() is False
    assert registry.stop_all() == {"optional_partial": None}


def test_optional_stop_failure_is_not_ready_and_remains_retryable():
    registry = TaskRegistry()
    attempts = 0

    def stop():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("join still pending")

    registry.register(
        TaskDescriptor("optional_worker", lambda: None, stop, required=False)
    )
    registry.start_all()

    assert registry.stop_all() == {"optional_worker": "join still pending"}
    assert registry.status()["optional_worker"] == TaskState.STOP_FAILED
    assert registry.is_ready() is False
    assert registry.stop_all() == {"optional_worker": None}
    assert registry.status()["optional_worker"] == TaskState.STOPPED


def test_shutdown_quiesces_dependency_before_dependent_stop_can_fail():
    events = []
    dependent_attempts = 0
    registry = TaskRegistry()

    registry.register(
        TaskDescriptor(
            "scheduler",
            lambda: None,
            lambda: events.append("scheduler:stop"),
            quiesce=lambda: events.append("scheduler:quiesce"),
        )
    )

    def stop_dependent():
        nonlocal dependent_attempts
        dependent_attempts += 1
        events.append(f"dependent:stop:{dependent_attempts}")
        if dependent_attempts == 1:
            raise RuntimeError("still draining")

    registry.register(
        TaskDescriptor(
            "dependent",
            lambda: None,
            stop_dependent,
            dependencies=("scheduler",),
        )
    )
    registry.start_all()

    first = registry.stop_all()
    assert first["dependent"] == "still draining"
    assert "active dependents" in first["scheduler"]
    assert events == ["scheduler:quiesce", "dependent:stop:1"]

    assert registry.stop_all() == {"dependent": None, "scheduler": None}
    assert events == [
        "scheduler:quiesce",
        "dependent:stop:1",
        "dependent:stop:2",
        "scheduler:stop",
    ]


def test_quiesce_failure_blocks_entire_stop_phase_until_retry():
    events = []
    quiesce_attempts = 0
    registry = TaskRegistry()

    def quiesce():
        nonlocal quiesce_attempts
        quiesce_attempts += 1
        events.append(f"quiesce:{quiesce_attempts}")
        if quiesce_attempts == 1:
            raise RuntimeError("admission still open")

    registry.register(
        TaskDescriptor(
            "scheduler",
            lambda: None,
            lambda: events.append("scheduler:stop"),
            quiesce=quiesce,
        )
    )
    registry.register(
        TaskDescriptor(
            "dependent",
            lambda: None,
            lambda: events.append("dependent:stop"),
            dependencies=("scheduler",),
        )
    )
    registry.start_all()

    assert registry.stop_all() == {"scheduler": "admission still open"}
    assert events == ["quiesce:1"]
    assert registry.is_ready() is False
    with pytest.raises(RuntimeError, match="shutdown is still in progress"):
        registry.start_all()
    assert registry.status() == {
        "scheduler": TaskState.RUNNING,
        "dependent": TaskState.RUNNING,
    }

    assert registry.stop_all() == {"dependent": None, "scheduler": None}
    assert events == [
        "quiesce:1",
        "quiesce:2",
        "dependent:stop",
        "scheduler:stop",
    ]
    assert registry.is_ready() is False

    # Only an explicit new start after every resource stopped opens the next
    # registry generation.
    registry.start_all()
    assert registry.is_ready() is True


def test_asgi_startup_cleanup_is_shielded_and_propagates_original(monkeypatch):
    import anyio
    from benchmark.bootstrap import app as app_module
    from benchmark.bootstrap.app import AppSettings, create_app

    handle = RuntimeHandle(StartupMode.FULL, TaskRegistry())
    start_error = RuntimeError("original start failure")
    bootstrap_error = RuntimeBootstrapError(
        start_error,
        handle,
        {"scheduler": "cleanup busy"},
    )
    cleanup_completed = []
    observed = []

    async def fail_bootstrap(context):
        raise bootstrap_error

    async def eventually_clean(runtime_handle, **kwargs):
        await anyio.sleep(0.05)
        cleanup_completed.append(True)

    monkeypatch.setattr(app_module, "bootstrap_runtime", fail_bootstrap)
    monkeypatch.setattr(app_module, "shutdown_runtime", eventually_clean)
    app = create_app(
        settings=AppSettings(startup_cleanup_timeout_seconds=0.5),
        mode=StartupMode.FULL,
    )

    async def run_lifespan():
        try:
            async with app.router.lifespan_context(app):
                pass
        except BaseException as error:
            observed.append(error)

    async def exercise_cancel():
        async with anyio.create_task_group() as group:
            group.start_soon(run_lifespan)
            await anyio.sleep(0.01)
            group.cancel_scope.cancel()

    anyio.run(exercise_cancel)
    assert cleanup_completed == [True]
    assert observed == [start_error]


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
    from services.scheduler import TaskScheduler

    monkeypatch.setenv("AI_TRADE_FIRST_EXECUTION_TIME", "2026-08-02T08:00:00+08:00")
    monkeypatch.setattr(
        scheduler,
        "_ensure_market_data_ready",
        lambda: (_ for _ in ()).throw(RuntimeError("market unavailable")),
    )
    # Use an isolated scheduler instance: shutting one down leaves its cancel
    # signal set until the next start(), and the global singleton must not
    # leak that state into other tests.
    fresh = TaskScheduler()
    monkeypatch.setattr(scheduler, "task_scheduler", fresh)

    # In the bootstrap order the scheduler task is running before
    # ai_auto_trading starts; reproduce that precondition here.
    fresh.start()
    try:
        with pytest.raises(RuntimeError, match="market unavailable"):
            scheduler.reset_auto_trading_job()
    finally:
        assert fresh.shutdown()


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
