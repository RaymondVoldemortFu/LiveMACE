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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import scheduler
from services.scheduler import (
    SchedulerBusyError,
    SchedulerNotRunningError,
    TaskScheduler,
)


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

    wrapped = ts._track_job(lambda: calls.append(1))
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
        threading.Thread(target=ts._track_job(job), daemon=True).start()
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
    wrapped = ts._track_job(lambda: calls.append(1))

    assert ts.shutdown(timeout=5) is True
    # Simulate APScheduler firing a job that was already submitted to the
    # executor before shutdown stopped it.
    wrapped()

    assert calls == []
    # A restart resets the signal and jobs run again.
    ts.start()
    try:
        wrapped()
        assert calls == [1]
    finally:
        assert ts.shutdown(timeout=5) is True


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

        wrapped = ts._track_job(task)
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
