"""Scheduler lifecycle contracts.

Covers the code-review P1 findings:
- business entry points must never start the global scheduler implicitly
  (NO_BACKGROUND stays background-free);
- shutdown provides a quiescence guarantee for jobs that already started;
- async account routes must not run the market warmup on the event loop.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import scheduler
from services.scheduler import (
    JobSpec,
    SchedulerBusyError,
    SchedulerNotRunningError,
    TaskScheduler,
)
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger


# ---------------------------------------------------------------------------
# NO_BACKGROUND: entry points must not start the global scheduler
# ---------------------------------------------------------------------------


def test_add_job_apis_require_running_scheduler():
    ts = TaskScheduler()
    with pytest.raises(SchedulerNotRunningError):
        ts.add_interval_task(lambda: None, 60, "interval_task")
    with pytest.raises(SchedulerNotRunningError):
        ts.add_date_task(lambda: None, datetime.now(timezone.utc), "date_task")
    with pytest.raises(SchedulerNotRunningError):
        ts.add_account_snapshot_task(1)
    with pytest.raises(SchedulerNotRunningError):
        ts.add_margin_monitor_task()
    with pytest.raises(SchedulerNotRunningError):
        ts.add_database_snapshot_task()
    assert not ts.is_running()


def test_add_account_snapshot_job_skips_when_scheduler_not_running(monkeypatch):
    fresh = TaskScheduler()
    monkeypatch.setattr(scheduler, "task_scheduler", fresh)

    scheduler.add_account_snapshot_job(7)

    assert not fresh.is_running()
    assert fresh.scheduler is None


def test_reset_auto_trading_job_skips_when_scheduler_not_running(monkeypatch):
    fresh = TaskScheduler()
    monkeypatch.setattr(scheduler, "task_scheduler", fresh)
    # No trading env is required for the skip path: the request must be
    # ignored before any warmup or configuration parsing happens.
    monkeypatch.delenv("AI_TRADE_FIRST_EXECUTION_TIME", raising=False)

    warmup_calls = []
    monkeypatch.setattr(
        scheduler, "_ensure_market_data_ready", lambda: warmup_calls.append(1)
    )

    scheduler.reset_auto_trading_job()

    assert not fresh.is_running()
    assert fresh.scheduler is None
    assert warmup_calls == []


def test_ws_register_does_not_start_scheduler(monkeypatch):
    from api import ws

    fresh = TaskScheduler()
    monkeypatch.setattr(scheduler, "task_scheduler", fresh)

    manager = ws.ConnectionManager()
    fake_ws = object()
    manager.register(7, fake_ws)
    try:
        assert not fresh.is_running()
        assert fresh.scheduler is None
    finally:
        manager.unregister(7, fake_ws)


# ---------------------------------------------------------------------------
# Shutdown quiescence
# ---------------------------------------------------------------------------


def _run_immediate_job(ts: TaskScheduler, job, job_id: str) -> None:
    ts.add_date_task(job, datetime.now(timezone.utc), job_id, misfire_grace_time=30)


def test_shutdown_waits_for_running_job_and_signals_cancellation():
    ts = TaskScheduler()
    ts.start()
    try:
        started = threading.Event()
        observed_cancellation = []

        def job():
            started.set()
            while not ts.cancellation_requested():
                time.sleep(0.01)
            observed_cancellation.append(True)

        _run_immediate_job(ts, job, "cooperative_job")
        assert started.wait(10), "job never started"

        assert ts.shutdown(timeout=10) is True
        # The job observed the cooperative cancel signal and finished
        # before shutdown returned.
        assert observed_cancellation == [True]
    finally:
        ts.shutdown(timeout=1)


def test_shutdown_reports_incomplete_when_job_does_not_stop():
    ts = TaskScheduler()
    ts.start()
    started = threading.Event()
    release = threading.Event()

    def job():
        started.set()
        release.wait(30)

    try:
        _run_immediate_job(ts, job, "blocking_job")
        assert started.wait(10), "job never started"

        result = ts.shutdown(timeout=0.2)

        assert result is False, "shutdown must not silently report success"
        assert ts.cancellation_requested()
    finally:
        release.set()


def test_stop_scheduler_raises_when_jobs_do_not_drain(monkeypatch):
    fresh = TaskScheduler()
    fresh.DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 0.2
    monkeypatch.setattr(scheduler, "task_scheduler", fresh)
    fresh.start()
    started = threading.Event()
    release = threading.Event()

    def job():
        started.set()
        release.wait(30)

    try:
        _run_immediate_job(fresh, job, "blocking_job")
        assert started.wait(10), "job never started"

        with pytest.raises(RuntimeError, match="incomplete"):
            scheduler.stop_scheduler()
    finally:
        release.set()


def test_jobs_skip_execution_after_cancellation_requested():
    ts = TaskScheduler()
    calls = []
    ts._cancel_event.set()

    wrapped = ts._track_job(lambda: calls.append(1), "cancel_skip")
    wrapped()

    assert calls == []


def test_second_shutdown_does_not_lie_while_job_still_running():
    """A timed-out shutdown must stay False on retry until jobs actually drain.

    Regression for the review finding: after the first shutdown timed out,
    a second call used to return True immediately because the underlying
    APScheduler object had already stopped, even with jobs still running.
    """
    ts = TaskScheduler()
    ts.start()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def job():
        started.set()
        release.wait(30)
        finished.set()

    try:
        _run_immediate_job(ts, job, "sticky_blocking_job")
        assert started.wait(10), "job never started"

        assert ts.shutdown(timeout=0.1) is False
        # Job is still blocked: retrying must keep reporting failure.
        assert ts.shutdown(timeout=0.1) is False
    finally:
        release.set()

    assert finished.wait(10), "job never finished after release"
    # Once the job drained, shutdown may finally report a clean stop.
    assert ts.shutdown(timeout=5) is True


def test_shutdown_retries_unfinished_apscheduler_cleanup(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    ts.add_interval_task(lambda: None, 3600, "preserved_until_clean")
    assert ts.scheduler is not None
    concrete = ts.scheduler
    jobstore = concrete._jobstores["default"]
    original_shutdown = jobstore.shutdown
    attempts = []

    def fail_once():
        attempts.append("jobstore")
        if len(attempts) == 1:
            raise RuntimeError("jobstore cleanup failed")
        return original_shutdown()

    monkeypatch.setattr(jobstore, "shutdown", fail_once)
    with pytest.raises(RuntimeError, match="jobstore cleanup failed"):
        ts.shutdown(timeout=1)

    assert concrete.running is False
    assert concrete.shutdown_complete is False
    assert ts._state == scheduler.SchedulerState.FAILED
    assert "preserved_until_clean" in ts._registrations

    assert ts.shutdown(timeout=1) is True
    assert attempts == ["jobstore", "jobstore"]
    assert concrete.shutdown_complete is True
    assert ts._state == scheduler.SchedulerState.STOPPED
    assert ts._registrations == {}


def test_shutdown_join_timeout_keeps_thread_handle_and_retries(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    concrete = ts.scheduler
    assert concrete is not None
    jobstore = concrete._jobstores["default"]
    jobstore_calls = []
    original_jobstore_shutdown = jobstore.shutdown
    monkeypatch.setattr(
        jobstore,
        "shutdown",
        lambda: jobstore_calls.append(1) or original_jobstore_shutdown(),
    )
    release = threading.Event()
    observed_join_timeouts = []
    real_thread = concrete._thread

    class StuckThread:
        ident = 1

        def is_alive(self):
            return not release.is_set()

        def join(self, timeout=None):
            observed_join_timeouts.append(timeout)
            if timeout is None:
                release.wait()
                return
            release.wait(timeout)

    try:
        concrete._thread = StuckThread()
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="APScheduler thread did not stop"):
            ts.shutdown(timeout=0.05)
        elapsed = time.monotonic() - started
        assert elapsed < 0.3
        assert observed_join_timeouts
        assert observed_join_timeouts[0] <= 0.05
        assert concrete._shutdown_thread_joined is False
        assert concrete._thread is not None
        assert concrete.shutdown_complete is False
        assert ts._state == scheduler.SchedulerState.FAILED
        assert jobstore_calls == []

        release.set()
        assert ts.shutdown(timeout=1) is True
        assert jobstore_calls == [1]
        assert concrete.shutdown_complete is True
        assert concrete._shutdown_thread_joined is True
        assert ts._state == scheduler.SchedulerState.STOPPED
    finally:
        release.set()
        if real_thread is not None and real_thread.is_alive():
            real_thread.join(timeout=1)



def test_shutdown_retry_resumes_after_completed_cleanup_phase(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    concrete = ts.scheduler
    executor = concrete._executors["default"]
    jobstore = concrete._jobstores["default"]
    executor_calls = []
    jobstore_calls = []
    original_executor_shutdown = executor.shutdown
    original_jobstore_shutdown = jobstore.shutdown

    def track_executor(wait=True):
        executor_calls.append(wait)
        return original_executor_shutdown(wait)

    def fail_jobstore_once():
        jobstore_calls.append(1)
        if len(jobstore_calls) == 1:
            raise RuntimeError("late cleanup failure")
        return original_jobstore_shutdown()

    monkeypatch.setattr(executor, "shutdown", track_executor)
    monkeypatch.setattr(jobstore, "shutdown", fail_jobstore_once)

    with pytest.raises(RuntimeError, match="late cleanup failure"):
        ts.shutdown(timeout=1)
    assert ts.shutdown(timeout=1) is True
    assert executor_calls == [False], "completed executor cleanup was repeated"
    assert jobstore_calls == [1, 1]


def test_start_rejected_while_previous_generation_still_inflight():
    """Incomplete shutdown must not clear cancel or overlap a new scheduler."""
    ts = TaskScheduler()
    ts.start()
    started = threading.Event()
    release = threading.Event()
    cancel_samples = []

    def job():
        started.set()
        while not release.is_set():
            cancel_samples.append(ts.cancellation_requested())
            time.sleep(0.02)

    try:
        threading.Thread(
            target=ts._track_job(job, "busy_generation_job"), daemon=True
        ).start()
        assert started.wait(10), "job never started"
        assert ts.shutdown(timeout=0.15) is False
        assert ts.cancellation_requested()
        assert ts._inflight_jobs > 0

        with pytest.raises(SchedulerBusyError, match="in-flight"):
            ts.start()

        assert ts.cancellation_requested(), "cancel must stay set until drain"
        assert not ts.is_running()
        before = len(cancel_samples)
        time.sleep(0.12)
        after_reject = cancel_samples[before:]
        assert after_reject and all(after_reject), (
            "old job must keep observing cancel=True after rejected start"
        )
    finally:
        release.set()

    assert ts.shutdown(timeout=5) is True
    ts.start()
    assert ts.is_running()
    assert not ts.cancellation_requested()
    ts.shutdown(timeout=5)


def test_late_job_start_after_clean_shutdown_is_skipped():
    """Jobs submitted before shutdown but starting after the drain check
    must observe the (still set) cancel signal and skip, otherwise the
    'clean shutdown' result would be a lie."""
    ts = TaskScheduler()
    ts.start()
    calls = []
    wrapped = ts._track_job(lambda: calls.append(1), "late_job")

    assert ts.shutdown(timeout=5) is True
    # Simulate APScheduler firing a job that was already submitted to the
    # executor before shutdown stopped it.
    wrapped()

    assert calls == []
    # A restart clears cancel but advances generation: the old wrapper must
    # still skip. Only wrappers registered under the new generation may run.
    ts.start()
    try:
        wrapped()
        assert calls == []
        fresh = ts._track_job(lambda: calls.append(1), "late_job")
        fresh()
        assert calls == [1]
    finally:
        assert ts.shutdown(timeout=5) is True


def test_stale_submitted_job_skipped_after_clean_shutdown_and_restart():
    """Submitted-but-not-started old jobs must not run after restart.

    Regression: clean shutdown saw inflight=0, start() cleared cancel, then
    an executor callback from the previous generation entered _track_job and
    ran overlapping the new scheduler.
    """
    ts = TaskScheduler()
    ts.start()
    body_ran = []
    release_enter = threading.Event()
    entered = threading.Event()

    stale = ts._track_job(lambda: body_ran.append(1), "stale_submitted")

    def delayed_executor_callback():
        entered.set()
        release_enter.wait(10)
        stale()

    worker = threading.Thread(target=delayed_executor_callback, daemon=True)
    worker.start()
    assert entered.wait(5), "delayed callback never armed"
    assert ts._inflight_jobs == 0

    assert ts.shutdown(timeout=5) is True
    ts.start()
    assert not ts.cancellation_requested()
    release_enter.set()
    worker.join(5)

    assert body_ran == [], "stale generation job ran after restart"
    assert ts.shutdown(timeout=5) is True


def test_remove_or_replace_invalidates_queued_wrapper_same_generation():
    """Queued wrappers must not run after remove/re-add in the same generation.

    Regression: reset_auto_trading_job removes and rebuilds trade tasks; an
    already-submitted callback from the old registration must not execute
    the previous configuration alongside the new one.
    """
    ts = TaskScheduler()
    ts.start()
    calls = []
    hold = threading.Event()
    armed = threading.Event()

    old = ts._track_job(lambda: calls.append("old"), "trade_job")

    def queued():
        armed.set()
        hold.wait(10)
        old()

    worker = threading.Thread(target=queued, daemon=True)
    worker.start()
    assert armed.wait(5)

    ts.remove_task("trade_job")
    new = ts._track_job(lambda: calls.append("new"), "trade_job")
    hold.set()
    worker.join(5)
    new()

    assert calls == ["new"], f"stale replaced job still ran: {calls}"
    assert ts._generation == 1
    assert ts.shutdown(timeout=5) is True


def test_concurrent_install_keeps_token_aligned_with_scheduler_job():
    """Token publish must stay aligned with the wrapper APScheduler installs.

    Regression: publishing the token before add_job allowed concurrent
    reset_auto_trading_job callers to leave a live job whose token no longer
    matched, so every trigger skipped forever.
    """
    ts = TaskScheduler()
    ts.start()
    errors = []

    def body_a():
        return "A"

    def body_b():
        return "B"

    def raced(fn):
        try:
            for _ in range(30):
                ts.add_interval_task(fn, 3600, "race_job")
                job = ts.scheduler.get_job("race_job")
                assert job is not None
                assert job.func() in {"A", "B"}
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=raced, args=(body_a,))
    t2 = threading.Thread(target=raced, args=(body_b,))
    t1.start()
    t2.start()
    t1.join(15)
    t2.join(15)

    assert errors == [], errors
    job = ts.scheduler.get_job("race_job")
    assert job is not None
    assert job.func() in {"A", "B"}
    assert ts.shutdown(timeout=5) is True


def test_add_competing_with_shutdown_is_serialized_by_lifecycle_state(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    assert ts.scheduler is not None
    entered_add = threading.Event()
    release_add = threading.Event()
    add_finished = threading.Event()
    shutdown_finished = threading.Event()
    errors = []
    original_add = ts.scheduler.add_job

    def blocked_add(**kwargs):
        entered_add.set()
        assert release_add.wait(5)
        return original_add(**kwargs)

    monkeypatch.setattr(ts.scheduler, "add_job", blocked_add)

    def add():
        try:
            ts.add_interval_task(lambda: None, 3600, "serialized_add")
        except Exception as exc:
            errors.append(exc)
        finally:
            add_finished.set()

    def stop():
        try:
            ts.shutdown(timeout=5)
        except Exception as exc:
            errors.append(exc)
        finally:
            shutdown_finished.set()

    add_thread = threading.Thread(target=add)
    add_thread.start()
    assert entered_add.wait(5)
    stop_thread = threading.Thread(target=stop)
    stop_thread.start()

    # shutdown cannot change generation/state in the middle of registration.
    time.sleep(0.05)
    assert not shutdown_finished.is_set()
    release_add.set()
    add_thread.join(5)
    stop_thread.join(5)

    assert add_finished.is_set() and shutdown_finished.is_set()
    assert errors == []
    assert not ts.is_running()
    ts.start()
    try:
        assert ts.scheduler.get_job("serialized_add") is None
    finally:
        assert ts.shutdown(timeout=5) is True


def test_remove_failure_preserves_job_admission_and_propagates(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    calls = []
    ts.add_interval_task(lambda: calls.append(1), 3600, "remove_failure")
    assert ts.scheduler is not None
    job = ts.scheduler.get_job("remove_failure")
    token = ts._job_tokens["remove_failure"]

    def fail_remove(job_id):
        raise RuntimeError("job store unavailable")

    monkeypatch.setattr(ts.scheduler, "remove_job", fail_remove)
    with pytest.raises(RuntimeError, match="job store unavailable"):
        ts.remove_task("remove_failure")

    assert ts._job_tokens["remove_failure"] == token
    assert ts._job_specs["remove_failure"].job_id == "remove_failure"
    job.func()
    assert calls == [1]

    # Restore the concrete method before cleanup.
    monkeypatch.undo()
    assert ts.shutdown(timeout=5) is True


def test_family_reconcile_failure_restores_complete_old_family(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    trigger = IntervalTrigger(seconds=3600)
    old_specs = (
        JobSpec("old_a", lambda: "old-a", trigger),
        JobSpec("old_b", lambda: "old-b", trigger),
    )
    ts.reconcile_jobs("trading", old_specs)
    assert ts.scheduler is not None
    original_add = ts.scheduler.add_job

    def fail_second_new(**kwargs):
        if kwargs["id"] == "new_b":
            raise RuntimeError("install failed")
        return original_add(**kwargs)

    monkeypatch.setattr(ts.scheduler, "add_job", fail_second_new)
    new_specs = (
        JobSpec("new_a", lambda: "new-a", trigger),
        JobSpec("new_b", lambda: "new-b", trigger),
    )
    with pytest.raises(RuntimeError, match="install failed"):
        ts.reconcile_jobs("trading", new_specs)

    assert ts._job_families["trading"] == {"old_a", "old_b"}
    assert set(ts._job_specs) == {"old_a", "old_b"}
    assert {job.id for job in ts.scheduler.get_jobs()} == {"old_a", "old_b"}
    assert {ts.scheduler.get_job(job_id).func() for job_id in ("old_a", "old_b")} == {
        "old-a",
        "old-b",
    }
    assert ts.shutdown(timeout=5) is True


def test_family_reconcile_failure_never_replays_consumed_one_shot(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    calls = []
    consumed = JobSpec(
        "first_once",
        lambda: calls.append("first"),
        scheduler.DateTrigger(run_date=datetime.now(timezone.utc)),
        misfire_grace_time=30,
    )
    ts.reconcile_jobs("trading", (consumed,))
    deadline = time.monotonic() + 5
    while calls != ["first"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == ["first"]
    assert ts.scheduler.get_job("first_once") is None
    assert "first_once" not in ts._job_specs

    original_add = ts.scheduler.add_job

    def fail_new(**kwargs):
        if kwargs["id"] == "new_bad":
            raise RuntimeError("install failed")
        return original_add(**kwargs)

    monkeypatch.setattr(ts.scheduler, "add_job", fail_new)
    with pytest.raises(RuntimeError, match="install failed"):
        ts.reconcile_jobs(
            "trading",
            (JobSpec("new_bad", lambda: None, IntervalTrigger(seconds=3600)),),
        )

    time.sleep(0.1)
    assert calls == ["first"]
    assert ts.scheduler.get_job("first_once") is None
    assert "first_once" not in ts._job_specs
    assert ts.shutdown(timeout=5) is True


def test_consumed_occurrence_is_idempotent_but_new_run_date_can_be_scheduled():
    ts = TaskScheduler()
    ts.start()
    calls = []
    first_run = datetime.now(timezone.utc)
    first = JobSpec(
        "first_once",
        lambda: calls.append("first"),
        scheduler.DateTrigger(run_date=first_run),
        misfire_grace_time=30,
    )
    ts.reconcile_jobs("trading", (first,))
    deadline = time.monotonic() + 5
    while calls != ["first"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == ["first"]

    # Re-declaring the same occurrence is configuration idempotency, not replay.
    ts.reconcile_jobs("trading", (first,))
    time.sleep(0.1)
    assert calls == ["first"]
    assert ts.scheduler.get_job("first_once") is None

    next_run = datetime.now(timezone.utc) + timedelta(seconds=1)
    second = JobSpec(
        "first_once",
        lambda: calls.append("second"),
        scheduler.DateTrigger(run_date=next_run),
        misfire_grace_time=30,
    )
    ts.reconcile_jobs("trading", (second,))
    assert ts.scheduler.get_job("first_once") is not None
    deadline = time.monotonic() + 5
    while calls != ["first", "second"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == ["first", "second"]
    assert ts.shutdown(timeout=5) is True


def test_job_spec_is_immutable_and_job_ids_have_one_family_owner():
    kwargs = {"value": 1}
    spec = JobSpec(
        "owned_job",
        lambda **values: values["value"],
        IntervalTrigger(seconds=3600),
        kwargs=kwargs,
    )
    kwargs["value"] = 2
    assert spec.kwargs["value"] == 1
    with pytest.raises(TypeError):
        spec.kwargs["value"] = 3

    ts = TaskScheduler()
    ts.start()
    try:
        ts.reconcile_jobs("first_family", (spec,))
        with pytest.raises(ValueError, match="owned by family:first_family"):
            ts.reconcile_jobs("second_family", (spec,))
        assert ts._job_families == {"first_family": {"owned_job"}}
        job = ts.scheduler.get_job("owned_job")
        assert job.func(**job.kwargs) == 1

        ts.add_interval_task(lambda: "standalone", 3600, "standalone_job")
        standalone_spec = JobSpec(
            "standalone_job", lambda: "replacement", IntervalTrigger(seconds=3600)
        )
        with pytest.raises(ValueError, match="owned by standalone"):
            ts.reconcile_jobs("second_family", (standalone_spec,))
        assert ts.scheduler.get_job("standalone_job").func() == "standalone"

        with pytest.raises(ValueError, match="owned by family:first_family"):
            ts.add_interval_task(lambda: "override", 3600, "owned_job")
        assert ts.scheduler.get_job("owned_job").func(**job.kwargs) == 1
        assert ts._job_families == {"first_family": {"owned_job"}}

        snapshot_spec = JobSpec(
            "snapshot_account_7",
            lambda: None,
            IntervalTrigger(seconds=3600),
        )
        ts.reconcile_jobs("snapshot-family", (snapshot_spec,))
        with pytest.raises(ValueError, match="owned by family:snapshot-family"):
            ts.add_account_snapshot_task(7)
    finally:
        assert ts.shutdown(timeout=5) is True


def test_due_date_job_runs_when_token_activated_before_add_job():
    """A misfired/past DateTrigger must not be skipped for an unpublished token."""
    ts = TaskScheduler()
    ts.start()
    ran = []

    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    ts.add_date_task(
        lambda: ran.append(1),
        past,
        "due_first_run",
        misfire_grace_time=30,
    )

    deadline = time.monotonic() + 5
    while not ran and time.monotonic() < deadline:
        time.sleep(0.05)

    assert ran == [1], "due date job was consumed without running"
    assert ts.shutdown(timeout=5) is True


def test_consumed_one_shot_is_not_replayed_after_scheduler_restart():
    ts = TaskScheduler()
    ran = []
    run_date = datetime.now(timezone.utc) - timedelta(seconds=1)

    def reconcile():
        ts.reconcile_jobs(
            "one-shot-family",
            (
                scheduler.JobSpec(
                    "durable_once",
                    lambda: ran.append(1),
                    DateTrigger(run_date=run_date),
                    misfire_grace_time=30,
                ),
            ),
        )

    ts.start()
    reconcile()
    deadline = time.monotonic() + 5
    while len(ran) < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ran == [1]
    assert ts.shutdown(timeout=5) is True

    ts.start()
    reconcile()
    time.sleep(0.2)
    assert ran == [1]
    assert ts.shutdown(timeout=5) is True


def test_sqlalchemy_occurrence_ledger_prevents_cross_instance_replay(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.connection import Base
    from services.scheduler import SqlAlchemyOneShotOccurrenceLedger

    engine = create_engine(f"sqlite:///{tmp_path / 'occurrences.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    run_date = datetime.now(timezone.utc) - timedelta(seconds=1)
    ran = []

    for _ in range(2):
        ts = TaskScheduler(SqlAlchemyOneShotOccurrenceLedger(factory))
        ts.start()
        ts.reconcile_jobs(
            "durable-family",
            (
                scheduler.JobSpec(
                    "durable_process_once",
                    lambda: ran.append(1),
                    DateTrigger(run_date=run_date),
                    misfire_grace_time=30,
                ),
            ),
        )
        deadline = time.monotonic() + 2
        while not ran and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ts.shutdown(timeout=5) is True

    assert ran == [1]
    engine.dispose()


def test_occurrence_ledger_does_not_hide_unrelated_integrity_error():
    from sqlalchemy.exc import IntegrityError
    from services.scheduler import (
        OneShotOccurrence,
        SqlAlchemyOneShotOccurrenceLedger,
    )

    class EmptyQuery:
        def filter(self, *args):
            return self

        def first(self):
            return None

    class BrokenSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def add(self, value):
            pass

        def commit(self):
            raise IntegrityError("insert", {}, RuntimeError("check failed"))

        def rollback(self):
            pass

        def query(self, *args):
            return EmptyQuery()

    ledger = SqlAlchemyOneShotOccurrenceLedger(BrokenSession)
    occurrence = OneShotOccurrence(
        "not-a-duplicate",
        datetime.now(timezone.utc),
    )

    with pytest.raises(IntegrityError, match="check failed"):
        ledger.consume(occurrence)


def test_scheduler_start_cleans_a_candidate_that_started_then_raised(monkeypatch):
    created = []

    class PartialStartScheduler(scheduler._LifecycleSafeBackgroundScheduler):
        def __init__(self):
            super().__init__()
            created.append(self)

        def start(self):
            super().start()
            raise RuntimeError("after start")

    monkeypatch.setattr(
        scheduler,
        "_LifecycleSafeBackgroundScheduler",
        PartialStartScheduler,
    )
    ts = TaskScheduler()

    with pytest.raises(RuntimeError, match="after start"):
        ts.start()

    assert len(created) == 1
    assert created[0].shutdown_complete is True
    assert ts.scheduler is None
    assert ts._state == scheduler.SchedulerState.STOPPED


def test_one_shot_ledger_io_does_not_hold_lifecycle_lock():
    entered = threading.Event()
    release = threading.Event()

    class BlockingLedger:
        def is_consumed(self, occurrence):
            return False

        def consume(self, occurrence):
            entered.set()
            assert release.wait(5)
            return True

    body_ran = threading.Event()
    ts = TaskScheduler(BlockingLedger())
    ts.start()
    ts.reconcile_jobs(
        "blocking-ledger",
        (
            scheduler.JobSpec(
                "blocking_once",
                body_ran.set,
                DateTrigger(run_date=datetime.now(timezone.utc) - timedelta(seconds=1)),
                misfire_grace_time=30,
            ),
        ),
    )
    assert entered.wait(2)

    started = time.monotonic()
    assert ts.shutdown(timeout=0.05) is False
    assert time.monotonic() - started < 0.5
    release.set()
    assert body_ran.wait(2), "a successfully claimed occurrence must be drained, not lost"
    deadline = time.monotonic() + 2
    while ts._inflight_jobs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ts.shutdown(timeout=2) is True


def test_family_reconcile_waits_for_admitted_one_shot_before_switching():
    claim_entered = threading.Event()
    release_claim = threading.Event()
    old_body_entered = threading.Event()
    release_old_body = threading.Event()
    calls = []

    class BlockingLedger:
        def is_consumed(self, occurrence):
            return False

        def consume(self, occurrence):
            if occurrence.job_id == "family_once":
                claim_entered.set()
                assert release_claim.wait(5)
            return True

    ts = TaskScheduler(BlockingLedger())
    ts.start()

    def old_body():
        calls.append("old:start")
        old_body_entered.set()
        assert release_old_body.wait(5)
        calls.append("old:end")

    ts.reconcile_jobs(
        "switch-family",
        (
            JobSpec(
                "family_once",
                old_body,
                DateTrigger(
                    run_date=datetime.now(timezone.utc) - timedelta(seconds=1)
                ),
                misfire_grace_time=30,
            ),
        ),
    )
    assert claim_entered.wait(2)

    new_run_date = datetime.now(timezone.utc) - timedelta(milliseconds=100)
    reconcile_done = threading.Event()

    def switch_config():
        ts.reconcile_jobs(
            "switch-family",
            (
                JobSpec(
                    "family_once",
                    lambda: calls.append("new"),
                    DateTrigger(run_date=new_run_date),
                    misfire_grace_time=30,
                ),
            ),
        )
        reconcile_done.set()

    thread = threading.Thread(target=switch_config)
    thread.start()
    release_claim.set()
    assert old_body_entered.wait(2)
    assert not reconcile_done.wait(0.1)
    release_old_body.set()
    assert reconcile_done.wait(2)
    thread.join(2)

    deadline = time.monotonic() + 2
    while "new" not in calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls == ["old:start", "old:end", "new"]
    assert ts.shutdown(timeout=2) is True


def test_family_callback_cannot_synchronously_reconcile_any_family():
    ts = TaskScheduler()
    ts.start()
    observed = []

    def callback():
        try:
            ts.reconcile_jobs("other-family", ())
        except Exception as exc:
            observed.append(exc)

    ts.reconcile_jobs(
        "self-family",
        (
            JobSpec(
                "self_reconcile",
                callback,
                IntervalTrigger(seconds=3600),
            ),
        ),
    )
    job = ts.scheduler.get_job("self_reconcile")
    assert job is not None

    invocation = threading.Thread(target=job.func, daemon=True)
    invocation.start()
    invocation.join(timeout=1)

    assert invocation.is_alive() is False
    assert len(observed) == 1
    assert "cannot synchronously reconcile family 'other-family'" in str(observed[0])
    assert ts._families_reconciling == set()
    assert ts._inflight_families == {}
    assert ts.shutdown(timeout=2) is True


def test_family_reconcile_hidden_helper_wait_times_out_without_barrier_leak():
    ts = TaskScheduler()
    ts.start()
    helper_errors = []
    callback_completed = threading.Event()

    def callback():
        def reconcile_from_helper():
            try:
                ts.reconcile_jobs("helper-family", (), timeout=0.05)
            except Exception as exc:
                helper_errors.append(exc)

        helper = threading.Thread(target=reconcile_from_helper)
        helper.start()
        helper.join(timeout=1)
        callback_completed.set()

    ts.reconcile_jobs(
        "helper-family",
        (
            JobSpec(
                "helper_reconcile",
                callback,
                IntervalTrigger(seconds=3600),
            ),
        ),
    )
    job = ts.scheduler.get_job("helper_reconcile")
    assert job is not None

    invocation = threading.Thread(target=job.func, daemon=True)
    invocation.start()
    invocation.join(timeout=1)

    assert callback_completed.is_set()
    assert len(helper_errors) == 1
    assert isinstance(helper_errors[0], SchedulerBusyError)
    assert "drain admitted callbacks" in str(helper_errors[0])
    assert ts._families_reconciling == set()
    assert ts._inflight_families == {}
    assert ts.shutdown(timeout=2) is True


def test_reconcile_timeout_releases_waiting_old_one_shot_without_losing_it():
    ts = TaskScheduler()
    ts.start()
    blocker_entered = threading.Event()
    release_blocker = threading.Event()
    once_ran = threading.Event()

    def blocker():
        blocker_entered.set()
        assert release_blocker.wait(2)

    ts.reconcile_jobs(
        "timeout-family",
        (
            JobSpec("blocker", blocker, IntervalTrigger(seconds=3600)),
            JobSpec(
                "waiting_once",
                once_ran.set,
                DateTrigger(
                    run_date=datetime.now(timezone.utc) + timedelta(seconds=0.1)
                ),
                misfire_grace_time=5,
            ),
        ),
    )
    blocker_job = ts.scheduler.get_job("blocker")
    assert blocker_job is not None
    blocker_thread = threading.Thread(target=blocker_job.func, daemon=True)
    blocker_thread.start()
    assert blocker_entered.wait(1)

    reconcile_errors = []

    def reconcile_with_deadline():
        try:
            ts.reconcile_jobs("timeout-family", (), timeout=0.25)
        except Exception as exc:
            reconcile_errors.append(exc)

    reconcile_thread = threading.Thread(target=reconcile_with_deadline)
    reconcile_thread.start()
    reconcile_thread.join(timeout=1)

    assert len(reconcile_errors) == 1
    assert isinstance(reconcile_errors[0], SchedulerBusyError)
    assert once_ran.wait(1), "old one-shot must resume after switch rollback"
    assert ts._families_reconciling == set()
    release_blocker.set()
    blocker_thread.join(timeout=1)
    assert ts.shutdown(timeout=2) is True


def test_quiesce_closes_new_callback_admission_but_keeps_control_plane():
    ts = TaskScheduler()
    ts.start()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def recurring():
        calls.append(1)
        entered.set()
        release.wait(1)

    ts.reconcile_jobs(
        "quiesce-family",
        (JobSpec("quiesce_job", recurring, IntervalTrigger(seconds=0.05)),),
    )
    assert entered.wait(1)

    ts.quiesce()
    assert ts.is_running() is False
    with pytest.raises(SchedulerNotRunningError):
        ts.add_interval_task(lambda: None, 3600, "late_business_job")
    with pytest.raises(SchedulerBusyError, match="quiesced scheduler generation"):
        ts.start()
    release.set()
    time.sleep(0.2)
    assert calls == [1]
    assert ts.is_control_plane_running() is True

    # Control-plane removal remains legal after admission is closed.
    ts.reconcile_jobs("quiesce-family", ())
    assert ts.shutdown(timeout=2) is True


def test_concurrent_shutdown_attempts_publish_results_in_order(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    assert ts.scheduler is not None
    original_shutdown = ts.scheduler.shutdown
    first_entered = threading.Event()
    release_first = threading.Event()
    attempts = 0

    def fail_first(wait=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_entered.set()
            assert release_first.wait(1)
            raise RuntimeError("first cleanup failed")
        return original_shutdown(wait)

    monkeypatch.setattr(ts.scheduler, "shutdown", fail_first)
    outcomes = []

    def stop():
        try:
            outcomes.append(ts.shutdown(timeout=1))
        except Exception as exc:
            outcomes.append(exc)

    first = threading.Thread(target=stop)
    second = threading.Thread(target=stop)
    first.start()
    assert first_entered.wait(1)
    second.start()
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(outcomes) == 2
    assert any(isinstance(value, RuntimeError) for value in outcomes)
    assert True in outcomes
    assert ts._state == scheduler.SchedulerState.STOPPED
    ts.start()
    assert ts.shutdown(timeout=2) is True


def test_shutdown_never_reports_clean_while_a_job_body_runs_after_return():
    """Race exerciser for the atomic check-and-register contract.

    Many wrappers fire concurrently with shutdown; whenever shutdown
    reports a clean stop, no job body may begin execution after that
    moment.
    """
    for _ in range(20):
        ts = TaskScheduler()
        ts.start()
        body_started_at: list[float] = []
        lock = threading.Lock()

        def task():
            with lock:
                body_started_at.append(time.monotonic())

        wrapped = ts._track_job(task, f"race_job_{_}")
        barrier = threading.Barrier(9)

        def fire():
            barrier.wait(5)
            wrapped()

        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()

        barrier.wait(5)
        clean = ts.shutdown(timeout=5)
        returned_at = time.monotonic()
        for t in threads:
            t.join(5)

        assert clean is True, "drain must succeed within the bound"
        late = [ts_ for ts_ in body_started_at if ts_ > returned_at]
        assert not late, "a job body started after shutdown reported clean"


# ---------------------------------------------------------------------------
# Async account routes must not block the event loop
# ---------------------------------------------------------------------------


class _CreateAccountDB:
    def __init__(self, user):
        self._user = user

    def query(self, model):
        return SimpleNamespace(
            filter=lambda *args: SimpleNamespace(first=lambda: self._user),
            first=lambda: self._user,
        )

    def add(self, obj):
        return None

    def commit(self):
        return None

    def refresh(self, obj):
        return None


def test_account_creation_runs_trading_reset_off_the_event_loop(monkeypatch):
    from api import account_routes
    from config.api_feature_config import ApiFeatureConfig

    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_ACCOUNT_CREATION_API", True)

    ran_on_event_loop = []

    def probe_reset():
        try:
            asyncio.get_running_loop()
            ran_on_event_loop.append(True)
        except RuntimeError:
            ran_on_event_loop.append(False)
        time.sleep(0.3)

    monkeypatch.setattr(scheduler, "reset_auto_trading_job", probe_reset)

    user = SimpleNamespace(id=7, username="default")
    db = _CreateAccountDB(user)

    async def run_with_heartbeat():
        heartbeats = [time.monotonic()]

        async def heartbeat():
            while True:
                await asyncio.sleep(0.01)
                heartbeats.append(time.monotonic())

        hb_task = asyncio.create_task(heartbeat())
        try:
            response = await account_routes.create_new_account({"name": "probe"}, db)
        finally:
            hb_task.cancel()
        return response, heartbeats

    response, heartbeats = asyncio.run(run_with_heartbeat())

    assert response["name"] == "probe"
    assert ran_on_event_loop == [False], "reset ran on the event loop thread"
    max_gap = max(b - a for a, b in zip(heartbeats, heartbeats[1:]))
    assert max_gap < 0.2, f"event loop was blocked for {max_gap:.3f}s"


def test_stop_auto_trading_jobs_skips_when_control_plane_is_down(monkeypatch):
    fresh = TaskScheduler()
    monkeypatch.setattr(scheduler, "task_scheduler", fresh)
    scheduler.stop_auto_trading_jobs()
    assert fresh.scheduler is None
    assert not fresh.is_control_plane_running()


def test_stop_auto_trading_jobs_reconciles_after_quiesce(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    monkeypatch.setattr(scheduler, "task_scheduler", ts)
    try:
        ts.reconcile_jobs(
            "auto_trading",
            (JobSpec("ai_trade_job", lambda: None, IntervalTrigger(seconds=60)),),
        )
        ts.quiesce()
        assert ts.is_running() is False
        assert ts.is_control_plane_running() is True

        scheduler.stop_auto_trading_jobs()

        assert "ai_trade_job" not in ts._registrations
        assert ts.scheduler.get_job("ai_trade_job") is None
        assert "auto_trading" not in ts._job_families
    finally:
        assert ts.shutdown(timeout=2) is True


def test_stop_auto_trading_jobs_propagates_family_barrier_timeout(monkeypatch):
    ts = TaskScheduler()
    ts.start()
    monkeypatch.setattr(scheduler, "task_scheduler", ts)
    ts.DEFAULT_RECONCILE_TIMEOUT_SECONDS = 0.2
    entered = threading.Event()
    release = threading.Event()

    def job():
        entered.set()
        release.wait(2)

    try:
        ts.reconcile_jobs(
            "auto_trading",
            (JobSpec("ai_trade_job", job, IntervalTrigger(seconds=0.05)),),
        )
        assert entered.wait(1)
        ts.quiesce()
        with pytest.raises(SchedulerBusyError):
            scheduler.stop_auto_trading_jobs()
        assert ts.scheduler.get_job("ai_trade_job") is not None
    finally:
        release.set()
        assert ts.shutdown(timeout=2) is True

