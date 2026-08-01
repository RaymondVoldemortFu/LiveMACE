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


class TaskState(str, Enum):
    REGISTERED = "registered"
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"
    SKIPPED = "skipped"


@dataclass
class _TaskRecord:
    descriptor: TaskDescriptor
    state: TaskState = TaskState.REGISTERED
    error: Optional[str] = None
    start_index: int = field(default=-1)


class TaskRegistry:
    """Thread-safe, idempotent start/stop for registered runtime tasks."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: Dict[str, _TaskRecord] = {}
        self._start_counter = 0

    def register(self, descriptor: TaskDescriptor) -> None:
        with self._lock:
            if descriptor.task_id in self._records:
                raise ValueError(f"duplicate runtime task id: {descriptor.task_id!r}")
            self._records[descriptor.task_id] = _TaskRecord(descriptor=descriptor)

    def start_all(self) -> None:
        """Start every registered task not yet running, in registration order.

        Raises on the first required-task failure (or required task blocked
        by a missing dependency); the caller owns cleanup of what already
        started (see ``runtime.bootstrap_runtime``).
        """
        with self._lock:
            for record in self._records.values():
                if record.state == TaskState.RUNNING:
                    continue
                descriptor = record.descriptor
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
                try:
                    descriptor.start()
                except Exception as exc:
                    record.state = TaskState.FAILED
                    record.error = str(exc)
                    if descriptor.required:
                        logger.error(
                            "required task %s failed to start: %s", descriptor.task_id, exc
                        )
                        raise
                    logger.error(
                        "optional task %s failed to start, continuing: %s",
                        descriptor.task_id,
                        exc,
                    )
                else:
                    record.state = TaskState.RUNNING
                    record.start_index = self._start_counter
                    self._start_counter += 1
                    logger.info("runtime task started: %s", descriptor.task_id)

    def stop_all(self) -> Dict[str, Optional[str]]:
        """Stop running tasks in reverse start order (idempotent).

        Returns ``{task_id: error_or_None}``; stop failures are recorded
        and logged, never silently dropped.
        """
        results: Dict[str, Optional[str]] = {}
        with self._lock:
            running = sorted(
                (r for r in self._records.values() if r.state == TaskState.RUNNING),
                key=lambda r: r.start_index,
                reverse=True,
            )
            for record in running:
                descriptor = record.descriptor
                if descriptor.stop is None:
                    record.state = TaskState.STOPPED
                    results[descriptor.task_id] = None
                    continue
                try:
                    descriptor.stop()
                except Exception as exc:
                    record.state = TaskState.FAILED
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
                if record.state == TaskState.FAILED:
                    out[task_id] = (
                        "required_failed" if record.descriptor.required else "degraded"
                    )
                else:
                    out[task_id] = record.state.value
            return out

    def is_ready(self) -> bool:
        with self._lock:
            return not any(
                r.state == TaskState.FAILED and r.descriptor.required
                for r in self._records.values()
            )


def _load_extension_catalog() -> None:
    """Extension catalog load slot (M13).

    The catalog must be deterministically loaded and frozen before any
    scheduler job can create an Agent. Until M13 lands this is a no-op
    hook so the ordering contract is already in place.
    """
    logger.info("extension catalog load: pending M13 integration (no-op)")


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

        ContainerService().shutdown()

    def _scheduler_start() -> None:
        from services.scheduler import start_scheduler

        start_scheduler()

    def _scheduler_stop() -> None:
        from services.scheduler import stop_scheduler

        stop_scheduler()

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

    def _eval_checkpoint_job() -> None:
        from services.startup import schedule_eval_checkpoint_job

        schedule_eval_checkpoint_job()

    return [
        TaskDescriptor("redis_tool_cache", _redis_tool_cache, required=True),
        TaskDescriptor("extension_catalog", _load_extension_catalog, required=True,
                       dependencies=("redis_tool_cache",)),
        TaskDescriptor("docker_sandbox", _docker_sandbox_start, _docker_sandbox_stop,
                       required=False),
        TaskDescriptor("scheduler", _scheduler_start, _scheduler_stop, required=True,
                       dependencies=("extension_catalog",)),
        TaskDescriptor("market_tasks", _market_tasks, required=True,
                       dependencies=("scheduler",)),
        TaskDescriptor("asset_curve_backfill_1h", _asset_curve_backfill, required=False),
        TaskDescriptor("ai_auto_trading", _auto_trading, _auto_trading_stop, required=True,
                       dependencies=("scheduler", "market_tasks")),
        TaskDescriptor("price_cache_cleanup", _price_cache_cleanup, required=False,
                       dependencies=("scheduler",)),
        TaskDescriptor("margin_monitor", _margin_monitor, required=True,
                       dependencies=("scheduler",)),
        TaskDescriptor("order_scheduler", _order_scheduler_start, _order_scheduler_stop,
                       required=False),
        TaskDescriptor("eval_checkpoint_job", _eval_checkpoint_job, required=False,
                       dependencies=("scheduler",)),
    ]
