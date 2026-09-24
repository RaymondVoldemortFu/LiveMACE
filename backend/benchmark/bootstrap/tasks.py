"""Background-task descriptors and lifecycle registry (M18).

Each background task declares id, start, stop, required and dependencies.
Registration order is start order; repeated start calls never double-start;
shutdown runs in reverse start order and never hides a stop failure.

The default descriptor table binds to the *current* service entry points
(job functions and intervals unchanged, per M18 file boundary), in the
fixed runtime order: Redis ready -> extension catalog -> Docker ->
scheduler jobs.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskDescriptor:
    """One managed runtime task.

    - ``required``: start failure aborts bootstrap (already-started tasks
      are stopped by the runtime); non-required failures are recorded and
      startup continues.
    - ``dependencies``: task ids that must be RUNNING first; otherwise
      this task is skipped (and that is fatal if it is required).
    """

    task_id: str
    start: Callable[[], None]
    stop: Optional[Callable[[], None]] = None
    required: bool = True
    dependencies: Tuple[str, ...] = ()
    owns_resources: Optional[Callable[[], bool]] = None
    quiesce: Optional[Callable[[], None]] = None


class TaskState(str, Enum):
    REGISTERED = "registered"
    RUNNING = "running"
    START_FAILED = "start_failed"
    START_CLEANUP_FAILED = "start_cleanup_failed"
    STOP_FAILED = "stop_failed"
    STOPPED = "stopped"
    SKIPPED = "skipped"


@dataclass
class _TaskRecord:
    descriptor: TaskDescriptor
    state: TaskState = TaskState.REGISTERED
    error: Optional[str] = None
    start_index: int = field(default=-1)
    quiesced: bool = False


class TaskRegistry:
    """Thread-safe, idempotent start/stop for registered runtime tasks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: Dict[str, _TaskRecord] = {}
        self._start_counter = 0
        self._shutdown_requested = False

    def register(self, descriptor: TaskDescriptor) -> None:
        with self._lock:
            if descriptor.task_id in self._records:
                raise ValueError(f"duplicate runtime task id: {descriptor.task_id!r}")
            if descriptor.owns_resources is not None and descriptor.stop is None:
                raise ValueError(
                    f"runtime task {descriptor.task_id!r} declares resource ownership "
                    "but has no stop callback"
                )
            self._records[descriptor.task_id] = _TaskRecord(descriptor=descriptor)

    def start_all(self) -> None:
        """Start every registered task not yet running, in registration order.

        Raises on the first required-task failure (or required task blocked
        by a missing dependency); the caller owns cleanup of what already
        started (see ``runtime.bootstrap_runtime``).
        """
        with self._lock:
            if self._shutdown_requested:
                active = [
                    record.descriptor.task_id
                    for record in self._records.values()
                    if record.state
                    in (
                        TaskState.RUNNING,
                        TaskState.START_CLEANUP_FAILED,
                        TaskState.STOP_FAILED,
                    )
                ]
                if active:
                    raise RuntimeError(
                        "runtime tasks have not completed shutdown; shutdown is "
                        f"still in progress for: {active!r}; retry stop_all() "
                        "before starting a new generation"
                    )
                # An explicit start after every owned resource reached a
                # terminal stopped/non-active state creates the next registry
                # generation. Quiesce itself is never reversed in-place.
                self._shutdown_requested = False
            cleanup_pending = [
                record.descriptor.task_id
                for record in self._records.values()
                if record.state
                in (TaskState.START_CLEANUP_FAILED, TaskState.STOP_FAILED)
            ]
            if cleanup_pending:
                raise RuntimeError(
                    "runtime tasks have not completed shutdown: "
                    f"{cleanup_pending!r}; retry stop_all() before start_all()"
                )
            for record in self._records.values():
                if record.state == TaskState.RUNNING:
                    continue
                descriptor = record.descriptor
                record.quiesced = False
                blocked = [
                    dep
                    for dep in descriptor.dependencies
                    if self._records.get(dep) is None
                    or self._records[dep].state != TaskState.RUNNING
                ]
                if blocked:
                    record.state = TaskState.SKIPPED
                    record.error = f"dependencies not running: {blocked}"
                    if descriptor.required:
                        raise RuntimeError(
                            f"required task {descriptor.task_id!r} blocked, "
                            f"dependencies not running: {blocked}"
                        )
                    logger.warning(
                        "task %s skipped, dependencies not running: %s",
                        descriptor.task_id,
                        blocked,
                    )
                    continue
                # Reserve lifecycle order before invoking start. A component
                # that partially starts and then raises still owns resources at
                # this exact position in the dependency/start sequence.
                record.start_index = self._start_counter
                self._start_counter += 1
                try:
                    descriptor.start()
                except Exception as exc:
                    probe_error: Optional[BaseException] = None
                    try:
                        owns_resources = (
                            bool(descriptor.owns_resources())
                            if descriptor.owns_resources is not None
                            else descriptor.stop is not None
                        )
                    except BaseException as owned_exc:
                        # Failure to prove that no resource exists must be
                        # treated conservatively as cleanup pending.
                        owns_resources = True
                        probe_error = owned_exc
                    record.state = (
                        TaskState.START_CLEANUP_FAILED
                        if owns_resources
                        else TaskState.START_FAILED
                    )
                    record.error = str(exc)
                    if probe_error is not None:
                        record.error += (
                            "; resource ownership probe failed: "
                            f"{type(probe_error).__name__}: {probe_error}"
                        )
                    if descriptor.required or owns_resources:
                        logger.error(
                            "task %s failed to start and cannot continue: %s",
                            descriptor.task_id,
                            exc,
                        )
                        raise
                    logger.error(
                        "optional task %s failed to start, continuing: %s",
                        descriptor.task_id,
                        exc,
                    )
                else:
                    record.state = TaskState.RUNNING
                    logger.info("runtime task started: %s", descriptor.task_id)

    def stop_all(self) -> Dict[str, Optional[str]]:
        """Stop tasks in reverse order without dismantling failed dependents.

        Returns ``{task_id: error_or_None}``; stop failures are recorded
        and remain eligible for every later retry until stop succeeds. A task
        is deferred while any direct dependent remains RUNNING/STOP_FAILED;
        this rule naturally preserves the complete transitive dependency chain.
        """
        results: Dict[str, Optional[str]] = {}
        with self._lock:
            self._shutdown_requested = True
            # Phase 1 closes admissions for every active resource before any
            # dependent-specific stop can fail. Dependencies may remain alive
            # for retry, but they can no longer execute new business work.
            quiesce_failed = False
            for record in self._records.values():
                if (
                    record.state
                    in (
                        TaskState.RUNNING,
                        TaskState.START_CLEANUP_FAILED,
                        TaskState.STOP_FAILED,
                    )
                    and record.descriptor.quiesce is not None
                    and not record.quiesced
                ):
                    try:
                        record.descriptor.quiesce()
                    except Exception as exc:
                        quiesce_failed = True
                        record.error = str(exc)
                        results[record.descriptor.task_id] = str(exc)
                        logger.error(
                            "runtime task %s failed to quiesce: %s",
                            record.descriptor.task_id,
                            exc,
                        )
                    else:
                        record.quiesced = True
            if quiesce_failed:
                # Quiesce is a global phase boundary. Starting destructive
                # dependency teardown while any admission gate may still be
                # open recreates the exact mixed running/stopped state this
                # protocol is meant to prevent. Retry the entire phase first.
                return results
            stoppable = sorted(
                (
                    r
                    for r in self._records.values()
                    if r.state
                    in (
                        TaskState.RUNNING,
                        TaskState.START_CLEANUP_FAILED,
                        TaskState.STOP_FAILED,
                    )
                ),
                key=lambda r: r.start_index,
                reverse=True,
            )
            for record in stoppable:
                descriptor = record.descriptor
                active_dependents = [
                    dependent.descriptor.task_id
                    for dependent in self._records.values()
                    if descriptor.task_id in dependent.descriptor.dependencies
                    and dependent.state
                    in (
                        TaskState.RUNNING,
                        TaskState.START_CLEANUP_FAILED,
                        TaskState.STOP_FAILED,
                    )
                ]
                if active_dependents:
                    results[descriptor.task_id] = (
                        "stop deferred; active dependents: "
                        + ", ".join(sorted(active_dependents))
                    )
                    logger.warning(
                        "runtime task %s stop deferred; active dependents: %s",
                        descriptor.task_id,
                        active_dependents,
                    )
                    continue
                if descriptor.stop is None:
                    if record.state in (
                        TaskState.START_CLEANUP_FAILED,
                        TaskState.STOP_FAILED,
                    ):
                        error = "cleanup required but task has no stop callback"
                        record.state = TaskState.STOP_FAILED
                        record.error = error
                        results[descriptor.task_id] = error
                        continue
                    record.state = TaskState.STOPPED
                    results[descriptor.task_id] = None
                    continue
                try:
                    descriptor.stop()
                except Exception as exc:
                    record.state = TaskState.STOP_FAILED
                    record.error = str(exc)
                    results[descriptor.task_id] = str(exc)
                    logger.error("runtime task %s failed to stop: %s", descriptor.task_id, exc)
                else:
                    record.state = TaskState.STOPPED
                    results[descriptor.task_id] = None
                    logger.info("runtime task stopped: %s", descriptor.task_id)
        return results

    def status(self) -> Dict[str, TaskState]:
        with self._lock:
            return {task_id: r.state for task_id, r in self._records.items()}

    def health(self) -> Dict[str, str]:
        """Readiness view consumed by the internal readiness service (M21)."""
        with self._lock:
            out: Dict[str, str] = {}
            for task_id, record in self._records.items():
                if record.state in (
                    TaskState.START_FAILED,
                    TaskState.START_CLEANUP_FAILED,
                ):
                    out[task_id] = (
                        "required_failed" if record.descriptor.required else "degraded"
                    )
                elif record.state == TaskState.STOP_FAILED:
                    out[task_id] = "stop_failed"
                else:
                    out[task_id] = record.state.value
            return out

    def is_ready(self) -> bool:
        with self._lock:
            return not self._shutdown_requested and not any(
                (
                    r.state
                    in (TaskState.START_CLEANUP_FAILED, TaskState.STOP_FAILED)
                    or (
                        r.state == TaskState.START_FAILED
                        and r.descriptor.required
                    )
                )
                for r in self._records.values()
            )


def _load_extension_catalog() -> None:
    """Load and freeze the shared catalog before scheduling decisions."""
    from benchmark.extensions.host import get_extension_runtime

    runtime = get_extension_runtime()
    for record in runtime.catalog.list_extensions():
        logger.info("extension id=%s version=%s status=%s", record.id, record.version, record.status)


def default_task_descriptors() -> List[TaskDescriptor]:
    """The current runtime task table, order and intervals unchanged.

    Order: Redis ready -> extension catalog -> Docker -> scheduler ->
    scheduler jobs (market tasks, backfill, auto trading, price cleanup,
    margin monitor, order scheduler, eval checkpoints).
    """

    def _redis_tool_cache() -> None:
        from services.tool_cache import tool_cache

        tool_cache.ensure_ready()

    def _docker_sandbox_start() -> None:
        from services.container_service import ContainerService

        ContainerService()

    def _docker_sandbox_stop() -> None:
        from services.container_service import ContainerService
        from services.trading_commands import _ai_trade_run_lock

        if not _ai_trade_run_lock.acquire(blocking=False):
            raise RuntimeError("Decision workers still own sandbox leases")
        try:
            ContainerService().shutdown()
        finally:
            _ai_trade_run_lock.release()

    def _scheduler_start() -> None:
        from services.scheduler import start_scheduler

        start_scheduler()

    def _scheduler_stop() -> None:
        from services.scheduler import stop_scheduler

        stop_scheduler()

    def _scheduler_quiesce() -> None:
        from services.scheduler import quiesce_scheduler

        quiesce_scheduler()

    def _scheduler_owns_resources() -> bool:
        from services.scheduler import task_scheduler

        return task_scheduler.has_pending_cleanup()

    def _market_tasks() -> None:
        from services.scheduler import setup_market_tasks

        setup_market_tasks()

    def _asset_curve_backfill() -> None:
        from services.asset_curve_cache_service import backfill_recent_1h_curve_on_startup

        points = backfill_recent_1h_curve_on_startup()
        logger.info("1h asset-curve startup backfill completed, points_written=%s", points)

    def _auto_trading() -> None:
        from services.scheduler import reset_auto_trading_job

        reset_auto_trading_job()

    def _auto_trading_stop() -> None:
        from services.scheduler import stop_auto_trading_jobs

        stop_auto_trading_jobs()

    def _price_cache_cleanup() -> None:
        from services.price_cache import clear_expired_prices
        from services.scheduler import task_scheduler

        task_scheduler.add_interval_task(
            task_func=clear_expired_prices,
            interval_seconds=120,
            task_id="price_cache_cleanup",
        )

    def _margin_monitor() -> None:
        from services.scheduler import start_margin_monitor

        start_margin_monitor(interval_seconds=5)

    def _order_scheduler_start() -> None:
        from services.order_scheduler import start_order_scheduler

        start_order_scheduler()

    def _order_scheduler_stop() -> None:
        from services.order_scheduler import stop_order_scheduler

        stop_order_scheduler()

    def _order_scheduler_owns_resources() -> bool:
        from services.order_scheduler import order_scheduler_has_pending_cleanup

        return order_scheduler_has_pending_cleanup()

    def _eval_checkpoint_job() -> None:
        from services.startup import schedule_eval_checkpoint_job

        schedule_eval_checkpoint_job()

    return [
        TaskDescriptor("redis_tool_cache", _redis_tool_cache, required=True),
        TaskDescriptor("extension_catalog", _load_extension_catalog, required=True,
                       dependencies=("redis_tool_cache",)),
        TaskDescriptor("docker_sandbox", _docker_sandbox_start, _docker_sandbox_stop,
                       required=False),
        TaskDescriptor(
            "scheduler",
            _scheduler_start,
            _scheduler_stop,
            required=True,
            dependencies=("extension_catalog",),
            owns_resources=_scheduler_owns_resources,
            quiesce=_scheduler_quiesce,
        ),
        TaskDescriptor("market_tasks", _market_tasks, required=True,
                       dependencies=("scheduler",)),
        TaskDescriptor("asset_curve_backfill_1h", _asset_curve_backfill, required=False),
        TaskDescriptor("ai_auto_trading", _auto_trading, _auto_trading_stop, required=True,
                       dependencies=("scheduler", "market_tasks")),
        TaskDescriptor("price_cache_cleanup", _price_cache_cleanup, required=False,
                       dependencies=("scheduler",)),
        TaskDescriptor("margin_monitor", _margin_monitor, required=True,
                       dependencies=("scheduler",)),
        TaskDescriptor(
            "order_scheduler",
            _order_scheduler_start,
            _order_scheduler_stop,
            required=False,
            owns_resources=_order_scheduler_owns_resources,
        ),
        TaskDescriptor("eval_checkpoint_job", _eval_checkpoint_job, required=False,
                       dependencies=("scheduler",)),
    ]
