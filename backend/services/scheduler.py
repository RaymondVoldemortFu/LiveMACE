"""
Scheduled task scheduler service
Used to manage WebSocket snapshot updates and other scheduled tasks
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN, SchedulerEvent
from apscheduler.schedulers.base import STATE_STOPPED
from apscheduler.schedulers import SchedulerNotRunningError as APSchedulerNotRunningError
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.orm import Session
from typing import Any, Dict, Set, Callable, Optional, List, Mapping
import functools
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from enum import Enum
from types import MappingProxyType

from database.connection import SessionLocal
from database.models import Position, CryptoPrice, Account, Order
from decimal import Decimal
from services.snapshot_service import create_snapshots_for_all_accounts

logger = logging.getLogger(__name__)


class _LifecycleSafeBackgroundScheduler(BackgroundScheduler):
    """APScheduler adapter whose shutdown cannot race DateTrigger cleanup.

    APScheduler 3.x clears MemoryJobStore before joining its scheduler thread.
    A due one-off job can therefore be submitted, have the store cleared, and
    then fail when that same scheduler thread performs its automatic removal.
    The adapter first stops and joins admission, then closes executors and job
    stores; DateTrigger cleanup is idempotent only during that stop window.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lifecycle_shutdown_in_progress = False
        self._lifecycle_cleanup_lock = threading.RLock()
        self._shutdown_thread_joined = False
        self._shutdown_executors: Set[str] = set()
        self._shutdown_jobstores: Set[str] = set()
        self._shutdown_event_dispatched = False
        self._shutdown_complete = False

    @property
    def shutdown_complete(self) -> bool:
        with self._lifecycle_cleanup_lock:
            return self._shutdown_complete

    def remove_job(self, job_id, jobstore=None):
        try:
            return super().remove_job(job_id, jobstore)
        except JobLookupError:
            # When shutdown flips APScheduler to STOPPED while a DateTrigger
            # processing cycle is finishing, upstream remove_job switches to
            # its pending-job path even though this is the scheduler thread's
            # normal one-off cleanup. Treat only that shutdown cleanup as the
            # idempotent removal it logically is. Public removals happen before
            # this flag and retain strict failure behavior.
            if self._lifecycle_shutdown_in_progress:
                return None
            raise

    def shutdown(self, wait: bool = True):
        """Retryable, phase-tracked APScheduler resource cleanup.

        APScheduler's public ``running`` flag only describes callback
        admission. It cannot prove that executor/job-store cleanup completed.
        Every successful phase is therefore recorded separately and retries
        resume from the first unfinished resource instead of trusting state.
        """
        with self._lifecycle_cleanup_lock:
            if self._shutdown_complete:
                return
            if self.state == STATE_STOPPED and not self._lifecycle_shutdown_in_progress:
                raise APSchedulerNotRunningError

            # Reverse APScheduler 3.x's unsafe ordering: stop and join the
            # scheduling thread before MemoryJobStore.shutdown() clears jobs.
            # wait=False still permits TaskScheduler's bounded job-body drain.
            if not self._lifecycle_shutdown_in_progress:
                self._lifecycle_shutdown_in_progress = True
                self.state = STATE_STOPPED
                self.wakeup()
            if not self._shutdown_thread_joined:
                thread = getattr(self, "_thread", None)
                if thread is not None:
                    thread.join()
                    del self._thread
                self._shutdown_thread_joined = True

            # Mark an individual resource complete only after its cleanup call
            # returns. A failure leaves that alias pending for the next retry.
            with self._executors_lock:
                for alias, executor in self._executors.items():
                    if alias not in self._shutdown_executors:
                        executor.shutdown(wait)
                        self._shutdown_executors.add(alias)
            with self._jobstores_lock:
                for alias, jobstore in self._jobstores.items():
                    if alias not in self._shutdown_jobstores:
                        jobstore.shutdown()
                        self._shutdown_jobstores.add(alias)

            if not self._shutdown_event_dispatched:
                self._dispatch_event(SchedulerEvent(EVENT_SCHEDULER_SHUTDOWN))
                self._shutdown_event_dispatched = True
            self._shutdown_complete = True
            self._logger.info("Scheduler has been shut down")


def plan_first_and_recurring_runs(
    first_dt: datetime,
    interval_seconds: int,
    now: Optional[datetime] = None,
) -> tuple[Optional[datetime], datetime]:
    """
    Plan first-run and recurring-run anchors.

    - If now <= first_dt: first run should happen at first_dt.
    - If first_dt < now < first_dt + interval: first run is late but still within
      first interval window, so keep a one-off first run for misfire catch-up.
    - If now >= first_dt + interval: first run window has passed, only keep recurring.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")

    anchor_now = now or datetime.now(timezone.utc)
    first_interval_end = first_dt + timedelta(seconds=interval_seconds)
    recurring_start = first_dt + timedelta(seconds=interval_seconds)

    if anchor_now <= first_dt:
        return first_dt, recurring_start
    if anchor_now < first_interval_end:
        return first_dt, recurring_start

    elapsed_seconds = (anchor_now - recurring_start).total_seconds()
    missed_intervals = int(elapsed_seconds // interval_seconds)
    if elapsed_seconds % interval_seconds != 0:
        missed_intervals += 1
    return None, recurring_start + timedelta(seconds=missed_intervals * interval_seconds)


class SchedulerNotRunningError(RuntimeError):
    """Raised when a job is added while the scheduler is not running.

    The scheduler lifecycle is owned by the runtime bootstrap
    (``benchmark.bootstrap``): only ``start_scheduler()`` may start it.
    Business entry points must never start the scheduler implicitly.
    """


class SchedulerBusyError(RuntimeError):
    """Raised when ``start()`` is attempted while a prior generation still has jobs.

    After an incomplete shutdown the cancel signal must stay set and a new
    scheduler must not be created until every previously tracked job exits;
    otherwise old jobs would observe a cleared cancel flag and overlap with
    the new generation.
    """


class SchedulerState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class JobLifetime(str, Enum):
    RECURRING = "recurring"
    ONE_SHOT = "one_shot"


class JobExecutionState(str, Enum):
    SCHEDULED = "scheduled"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class JobOwner:
    """Exclusive owner of a scheduler registration."""

    family: Optional[str] = None

    @classmethod
    def standalone(cls) -> "JobOwner":
        return cls()

    @classmethod
    def for_family(cls, family: str) -> "JobOwner":
        return cls(family=family)

    @property
    def label(self) -> str:
        return "standalone" if self.family is None else f"family:{self.family}"


@dataclass(frozen=True)
class JobSpec:
    """Application-owned description of one APScheduler job."""

    job_id: str
    func: Callable
    trigger: Any
    args: tuple = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    max_instances: int = 1
    coalesce: bool = True
    misfire_grace_time: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", MappingProxyType(dict(self.kwargs)))


@dataclass(frozen=True)
class JobRegistration:
    """Application-side scheduling truth, including ownership and terminal state."""

    spec: JobSpec
    owner: JobOwner
    lifetime: JobLifetime
    state: JobExecutionState = JobExecutionState.SCHEDULED


class TaskScheduler:
    """Unified task scheduler"""

    #: Bounded wait (seconds) for in-flight jobs to drain during shutdown.
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 10.0

    def __init__(self):
        self.scheduler: Optional[BackgroundScheduler] = None
        self._started = False
        self._state = SchedulerState.STOPPED
        self._account_connections: Dict[int, Set] = {}  # track account connections
        # Cooperative cancellation signal observed by running jobs; set on
        # shutdown so that jobs stop at their next check point.
        self._cancel_event = threading.Event()
        # In-flight job tracking used to provide a quiescence guarantee on
        # shutdown: every scheduled job runs through _run_tracked_job.
        self._lifecycle_lock = threading.RLock()
        self._inflight_condition = threading.Condition(self._lifecycle_lock)
        self._inflight_jobs = 0
        # Bumped on every successful start(). Wrappers capture the generation
        # at registration time so jobs already submitted to an old executor
        # cannot run after a clean shutdown + restart clears the cancel flag.
        self._generation = 0
        # Per-task registration tokens: remove/replace bumps the active token so
        # already-queued wrappers for that task_id become stale within the same
        # scheduler generation (e.g. reset_auto_trading_job).
        self._job_tokens: Dict[str, int] = {}
        self._job_token_seq = 0
        self._registrations: Dict[str, JobRegistration] = {}

    @property
    def _job_specs(self) -> Dict[str, JobSpec]:
        """Compatibility/debug view of registrations that remain schedulable."""
        return {
            job_id: registration.spec
            for job_id, registration in self._registrations.items()
            if registration.state == JobExecutionState.SCHEDULED
        }

    @property
    def _job_families(self) -> Dict[str, Set[str]]:
        """Compatibility/debug view of active family membership."""
        families: Dict[str, Set[str]] = {}
        for job_id, registration in self._registrations.items():
            if (
                registration.owner.family is not None
                and registration.state == JobExecutionState.SCHEDULED
            ):
                families.setdefault(registration.owner.family, set()).add(job_id)
        return families

    def start(self):
        """Start the scheduler.

        Refuses to clear the cancel signal or create a new APScheduler while
        any job from a previous generation is still in flight. Callers must
        finish draining (retry ``shutdown``) before restarting.

        Each successful start advances ``_generation``. Job wrappers created
        under a prior generation are treated as stale even if cancel was
        cleared, covering APScheduler callbacks that were submitted before
        shutdown but only begin running after the next start.
        """
        with self._inflight_condition:
            if self._state == SchedulerState.RUNNING:
                return
            if self._state == SchedulerState.STOPPING or self._inflight_jobs > 0:
                raise SchedulerBusyError(
                    "cannot start scheduler: previous generation still has "
                    f"{self._inflight_jobs} in-flight job(s); drain via "
                    "shutdown() before restarting"
                )
            # Clear cancel only after the quiescence check above, under the
            # same condition jobs use to register, so a drained generation
            # cannot race a new start. Advance generation in the same
            # critical section so stale wrappers cannot observe a cleared
            # cancel belonging to their own generation.
            if self._state == SchedulerState.FAILED:
                raise SchedulerBusyError(
                    "cannot start scheduler: failed generation must complete "
                    "shutdown before restart"
                )
            self._generation += 1
            self._job_tokens.clear()
            self._registrations.clear()
            # A generation owns its own event. Never clear an event captured by
            # callbacks from an earlier generation.
            self._cancel_event = threading.Event()
            self.scheduler = _LifecycleSafeBackgroundScheduler()
            self.scheduler.start()
            self._started = True
            self._state = SchedulerState.RUNNING
            logger.info("Scheduler started")
    
    def shutdown(self, timeout: Optional[float] = None) -> bool:
        """Shutdown the scheduler with a quiescence guarantee.

        Signals cooperative cancellation to running jobs, stops future
        schedules, then waits up to ``timeout`` seconds for in-flight jobs
        to finish. Returns True when no job is still running; returns False
        (after logging an error) when running jobs could not be drained in
        time — callers must not treat that as a clean stop.

        The cancel signal stays set after shutdown (whether it drained or
        not) and is only reset by the next successful ``start()``: jobs that
        APScheduler already submitted but that begin running after the drain
        check must still observe the signal and skip, otherwise "clean
        shutdown" would be a lie. ``start()`` itself refuses to clear the
        signal while ``_inflight_jobs > 0``. The next successful ``start()``
        also advances ``_generation`` so stale executor callbacks cannot run
        after a restart. Repeated calls keep honouring jobs that are still
        running from a previous timed-out shutdown instead of reporting
        success just because the underlying scheduler object already stopped.
        """
        if timeout is None:
            timeout = self.DEFAULT_SHUTDOWN_TIMEOUT_SECONDS

        with self._inflight_condition:
            if self._state == SchedulerState.STOPPED and self._inflight_jobs == 0:
                # Never started (or already drained by a previous shutdown):
                # nothing to cancel or wait for.
                return True
            # Set the cancel signal inside the same critical section that
            # jobs use for their check-and-register step, so a job either
            # registered before this point (and is awaited below) or is
            # guaranteed to observe the signal and skip.
            self._cancel_event.set()
            # Mark stopped under the same lock so start() cannot observe
            # `_started is False` and clear cancel while we still expect to
            # drain this generation.
            self._started = False
            self._state = SchedulerState.STOPPING
            scheduler = self.scheduler

        shutdown_error: Optional[BaseException] = None
        if scheduler is None:
            shutdown_complete = True
        elif hasattr(scheduler, "shutdown_complete"):
            shutdown_complete = bool(scheduler.shutdown_complete)
        else:
            # Compatibility for scheduler test doubles predating the adapter.
            shutdown_complete = not scheduler.running
        if scheduler is not None and not shutdown_complete:
            try:
                scheduler.shutdown(wait=False)
            except BaseException as exc:
                shutdown_error = exc

        deadline = time.monotonic() + max(timeout, 0.0)
        with self._inflight_condition:
            while self._inflight_jobs > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._inflight_condition.wait(remaining)
            still_running = self._inflight_jobs

        if shutdown_error is not None:
            with self._inflight_condition:
                self._state = SchedulerState.FAILED
            logger.error(
                "APScheduler shutdown failed",
                exc_info=(
                    type(shutdown_error),
                    shutdown_error,
                    shutdown_error.__traceback__,
                ),
            )
            raise shutdown_error
        if still_running:
            logger.error(
                "Scheduler shutdown incomplete: %s job(s) still running after %.1fs",
                still_running,
                timeout,
            )
            return False
        with self._inflight_condition:
            self._state = SchedulerState.STOPPED
            self._job_tokens.clear()
            self._registrations.clear()
        logger.info("Scheduler shutdown")
        return True
    
    def is_running(self) -> bool:
        """Check if scheduler is running"""
        with self._inflight_condition:
            return bool(
                self._state == SchedulerState.RUNNING
                and self.scheduler
                and self.scheduler.running
            )

    def cancellation_requested(self) -> bool:
        """True once shutdown has been requested; jobs should stop cooperatively."""
        with self._inflight_condition:
            return self._cancel_event.is_set()

    def _require_running(self, action: str) -> None:
        if self._state != SchedulerState.RUNNING or not (
            self.scheduler and self.scheduler.running
        ):
            raise SchedulerNotRunningError(
                f"cannot {action}: scheduler is not running "
                "(scheduler startup is owned by the runtime bootstrap)"
            )

    def _allocate_tracked_job(
        self,
        task_func: Callable,
        task_id: str,
        lifetime: JobLifetime = JobLifetime.RECURRING,
    ) -> tuple[Callable, int]:
        """Build a tracked wrapper under the scheduler lifecycle lock."""
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a non-empty string")

        self._job_token_seq += 1
        job_token = self._job_token_seq
        job_generation = self._generation
        cancel_event = self._cancel_event

        @functools.wraps(task_func)
        def _run_tracked_job(*args, **kwargs):
            # Cancel / generation / token check and in-flight registration
            # must be atomic with shutdown, start, and remove/replace.
            with self._inflight_condition:
                if (
                    cancel_event.is_set()
                    or self._state != SchedulerState.RUNNING
                    or job_generation != self._generation
                    or self._job_tokens.get(task_id) != job_token
                ):
                    logger.info(
                        "Skipping job %s (%s): cancelled, stale generation, or replaced",
                        task_id,
                        getattr(task_func, "__name__", task_func),
                    )
                    return
                if lifetime == JobLifetime.ONE_SHOT:
                    registration = self._registrations.get(task_id)
                    if (
                        registration is not None
                        and registration.state == JobExecutionState.SCHEDULED
                    ):
                        # APScheduler consumes DateTrigger jobs after submission.
                        # Mirror that terminal transition under the same lock as
                        # admission/reconcile so rollback can never resurrect it.
                        self._registrations[task_id] = JobRegistration(
                            spec=registration.spec,
                            owner=registration.owner,
                            lifetime=registration.lifetime,
                            state=JobExecutionState.CONSUMED,
                        )
                        self._job_tokens.pop(task_id, None)
                self._inflight_jobs += 1
            try:
                return task_func(*args, **kwargs)
            finally:
                with self._inflight_condition:
                    self._inflight_jobs -= 1
                    self._inflight_condition.notify_all()

        return _run_tracked_job, job_token

    def _track_job(self, task_func: Callable, task_id: str) -> Callable:
        """Allocate and activate a tracked wrapper (tests/direct invocation)."""
        with self._inflight_condition:
            wrapper, job_token = self._allocate_tracked_job(task_func, task_id)
            self._job_tokens[task_id] = job_token
            return wrapper

    def _install_tracked_job(
        self,
        task_func: Callable,
        task_id: str,
        *,
        trigger,
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
        max_instances: int = 1,
        coalesce: bool = True,
        misfire_grace_time: Optional[int] = None,
    ) -> None:
        """Atomically register a job through the single lifecycle lock."""
        spec = JobSpec(
            job_id=task_id,
            func=task_func,
            trigger=trigger,
            args=tuple(args or ()),
            kwargs=dict(kwargs or {}),
            max_instances=max_instances,
            coalesce=coalesce,
            misfire_grace_time=misfire_grace_time,
        )
        with self._inflight_condition:
            self._require_running(f"install task {task_id!r}")
            self._install_spec_locked(spec, JobOwner.standalone())

    @staticmethod
    def _lifetime_for_spec(spec: JobSpec) -> JobLifetime:
        return (
            JobLifetime.ONE_SHOT
            if isinstance(spec.trigger, DateTrigger)
            else JobLifetime.RECURRING
        )

    @staticmethod
    def _same_one_shot_occurrence(left: JobSpec, right: JobSpec) -> bool:
        return (
            isinstance(left.trigger, DateTrigger)
            and isinstance(right.trigger, DateTrigger)
            and left.trigger.run_date == right.trigger.run_date
        )

    def _assert_owner_locked(self, job_id: str, owner: JobOwner) -> None:
        existing = self._registrations.get(job_id)
        if existing is not None and existing.owner != owner:
            raise ValueError(
                f"job {job_id!r} is owned by {existing.owner.label}, "
                f"not {owner.label}"
            )

    def _install_spec_locked(self, spec: JobSpec, owner: JobOwner) -> None:
        """Install one spec while holding the lifecycle lock.

        The provisional token is not observable by any other thread because
        callbacks, shutdown and all registry operations acquire the same lock.
        It is published before ``add_job`` only so a synchronous test double
        (or future executor) cannot consume an already-due DateTrigger.
        """
        assert self.scheduler is not None
        self._assert_owner_locked(spec.job_id, owner)
        lifetime = self._lifetime_for_spec(spec)
        previous_token = self._job_tokens.get(spec.job_id)
        previous_registration = self._registrations.get(spec.job_id)
        if (
            previous_registration is not None
            and previous_registration.state == JobExecutionState.CONSUMED
            and self._same_one_shot_occurrence(previous_registration.spec, spec)
        ):
            # Idempotent re-declaration of a consumed occurrence. This applies
            # to every installation path, not only family reconciliation.
            return
        wrapper, token = self._allocate_tracked_job(
            spec.func, spec.job_id, lifetime
        )
        self._job_tokens[spec.job_id] = token
        self._registrations[spec.job_id] = JobRegistration(
            spec=spec,
            owner=owner,
            lifetime=lifetime,
        )
        add_kwargs: dict = {
            "func": wrapper,
            "trigger": spec.trigger,
            "id": spec.job_id,
            "replace_existing": True,
            "max_instances": spec.max_instances,
            "coalesce": spec.coalesce,
        }
        if spec.args:
            add_kwargs["args"] = list(spec.args)
        if spec.kwargs:
            add_kwargs["kwargs"] = dict(spec.kwargs)
        if spec.misfire_grace_time is not None:
            add_kwargs["misfire_grace_time"] = spec.misfire_grace_time
        try:
            self.scheduler.add_job(**add_kwargs)
        except Exception:
            if previous_token is None:
                self._job_tokens.pop(spec.job_id, None)
            else:
                self._job_tokens[spec.job_id] = previous_token
            if previous_registration is None:
                self._registrations.pop(spec.job_id, None)
            else:
                self._registrations[spec.job_id] = previous_registration
            raise

    def _remove_task_locked(
        self,
        task_id: str,
        expected_owner: Optional[JobOwner] = None,
    ) -> bool:
        """Remove from APScheduler first, then invalidate local admission."""
        if self.scheduler is None:
            return False
        registration = self._registrations.get(task_id)
        if (
            expected_owner is not None
            and registration is not None
            and registration.owner != expected_owner
        ):
            raise ValueError(
                f"job {task_id!r} is owned by {registration.owner.label}, "
                f"not {expected_owner.label}"
            )
        try:
            self.scheduler.remove_job(task_id)
        except JobLookupError:
            existed = False
        else:
            existed = True
        self._job_tokens.pop(task_id, None)
        self._registrations.pop(task_id, None)
        return existed

    def reconcile_jobs(self, family: str, specs: tuple[JobSpec, ...]) -> None:
        """Atomically replace a logical family, restoring the old family on failure."""
        if not family:
            raise ValueError("family must be non-empty")
        ids = [spec.job_id for spec in specs]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate job id in family {family!r}")
        with self._inflight_condition:
            self._require_running(f"reconcile job family {family!r}")
            assert self.scheduler is not None
            owner = JobOwner.for_family(family)
            for job_id in ids:
                self._assert_owner_locked(job_id, owner)
            old_registrations = {
                job_id: registration
                for job_id, registration in self._registrations.items()
                if registration.owner == owner
            }
            self.scheduler.pause()
            try:
                for job_id, registration in tuple(old_registrations.items()):
                    if registration.state == JobExecutionState.SCHEDULED:
                        self._remove_task_locked(job_id, owner)
                    else:
                        self._registrations.pop(job_id, None)
                for spec in specs:
                    previous = old_registrations.get(spec.job_id)
                    if (
                        previous is not None
                        and previous.state == JobExecutionState.CONSUMED
                        and self._same_one_shot_occurrence(previous.spec, spec)
                    ):
                        # Reconciliation describes desired configuration, not
                        # permission to replay an already consumed occurrence.
                        self._registrations[spec.job_id] = previous
                    else:
                        self._install_spec_locked(spec, owner)
            except Exception as install_error:
                restore_errors = []
                current_family_ids = [
                    job_id
                    for job_id, registration in self._registrations.items()
                    if registration.owner == owner
                ]
                for job_id in current_family_ids:
                    try:
                        registration = self._registrations.get(job_id)
                        if (
                            registration is not None
                            and registration.state == JobExecutionState.SCHEDULED
                        ):
                            self._remove_task_locked(job_id, owner)
                        else:
                            self._registrations.pop(job_id, None)
                    except Exception as exc:
                        restore_errors.append(exc)
                for old_registration in old_registrations.values():
                    try:
                        if old_registration.state == JobExecutionState.CONSUMED:
                            self._registrations[
                                old_registration.spec.job_id
                            ] = old_registration
                        else:
                            self._install_spec_locked(
                                old_registration.spec, owner
                            )
                    except Exception as exc:
                        restore_errors.append(exc)
                if restore_errors:
                    self._state = SchedulerState.FAILED
                    self._started = False
                    self._cancel_event.set()
                    raise SchedulerBusyError(
                        f"failed to reconcile {family!r} and restore previous jobs"
                    ) from install_error
                raise
            finally:
                if self._state == SchedulerState.RUNNING:
                    self.scheduler.resume()

    def add_account_snapshot_task(self, account_id: int, interval_seconds: int = 10):
        """
        Add snapshot update task for account

        Args:
            account_id: Account ID
            interval_seconds: Update interval (seconds), default 10 seconds
        """
        job_id = f"snapshot_account_{account_id}"
        with self._inflight_condition:
            self._require_running("add account snapshot task")
            if self.scheduler.get_job(job_id):
                logger.debug(f"Snapshot task for account {account_id} already exists")
                return
            self._install_tracked_job(
                self._execute_account_snapshot,
                job_id,
                trigger=IntervalTrigger(seconds=interval_seconds),
                args=[account_id],
                misfire_grace_time=5,
            )

        logger.info(f"Added snapshot task for account {account_id}, interval {interval_seconds} seconds")

    def add_margin_monitor_task(self, interval_seconds: int = 5):
        """
        Add margin monitoring task to watch for liquidation conditions
        Checks all leveraged positions and force closes if margin is insufficient

        Args:
            interval_seconds: Check interval (seconds), default 5 seconds
        """
        job_id = "margin_monitor"
        with self._inflight_condition:
            self._require_running("add margin monitor task")
            if self.scheduler.get_job(job_id):
                logger.debug("Margin monitor task already exists")
                return
            self._install_tracked_job(
                self._check_margin_levels,
                job_id,
                trigger=IntervalTrigger(seconds=interval_seconds),
                misfire_grace_time=2,
            )

        logger.info(f"Added margin monitor task, checking every {interval_seconds} seconds")

    def add_database_snapshot_task(self, interval_seconds: int = 3600):
        """
        Add database snapshot task for all accounts
        Creates account_snapshots records for evaluation metrics calculation

        Args:
            interval_seconds: Snapshot interval (seconds), default 3600 (1 hour)
        """
        job_id = "database_snapshot_all_accounts"
        with self._inflight_condition:
            self._require_running("add database snapshot task")
            if self.scheduler.get_job(job_id):
                logger.debug("Database snapshot task already exists")
                return
            self._install_tracked_job(
                self._create_database_snapshots,
                job_id,
                trigger=IntervalTrigger(seconds=interval_seconds),
                misfire_grace_time=60,
            )

        logger.info(f"Added database snapshot task, interval {interval_seconds} seconds")

    def remove_account_snapshot_task(self, account_id: int):
        """
        Remove snapshot update task for account

        Args:
            account_id: Account ID
        """
        job_id = f"snapshot_account_{account_id}"
        with self._inflight_condition:
            if self._remove_task_locked(job_id, JobOwner.standalone()):
                logger.info(f"Removed snapshot task for account {account_id}")

    def add_interval_task(
        self,
        task_func: Callable,
        interval_seconds: int,
        task_id: str,
        *args,
        start_date: Optional[datetime] = None,
        **kwargs,
    ):
        """
        Add interval execution task

        Args:
            task_func: Function to execute
            interval_seconds: Execution interval (seconds)
            task_id: Task unique identifier
            *args, **kwargs: Parameters passed to task_func
        """
        self._install_tracked_job(
            task_func,
            task_id,
            trigger=IntervalTrigger(seconds=interval_seconds, start_date=start_date),
            args=list(args) if args else None,
            kwargs=dict(kwargs) if kwargs else None,
        )

        logger.info(f"Added interval task {task_id}: Execute every {interval_seconds} seconds")

    def add_date_task(
        self,
        task_func: Callable,
        run_date: datetime,
        task_id: str,
        *args,
        misfire_grace_time: Optional[int] = None,
        **kwargs,
    ):
        """Add one-off execution task at a fixed datetime."""
        self._install_tracked_job(
            task_func,
            task_id,
            trigger=DateTrigger(run_date=run_date),
            args=list(args) if args else None,
            kwargs=dict(kwargs) if kwargs else None,
            misfire_grace_time=misfire_grace_time,
        )

        logger.info(f"Added date task {task_id}: run at {run_date.isoformat()}")

    def remove_task(self, task_id: str):
        """
        Remove specified task

        Args:
            task_id: Task ID
        """
        with self._inflight_condition:
            if self._remove_task_locked(task_id, JobOwner.standalone()):
                logger.info(f"Removed task: {task_id}")

    def get_job_info(self) -> list:
        """Get all task information"""
        with self._inflight_condition:
            if not self.scheduler:
                return []
            return [
                {
                    "id": job.id,
                    "next_run_time": job.next_run_time,
                    "func_name": job.func.__name__
                    if hasattr(job.func, "__name__")
                    else str(job.func),
                }
                for job in self.scheduler.get_jobs()
            ]

    def _execute_account_snapshot(self, account_id: int):
        """
        Internal method to execute account snapshot update

        Args:
            account_id: Account ID
        """
        start_time = datetime.now()
        try:
            # Dynamic import to avoid circular dependency
            from api.ws import manager

            # Check if account still has active connections
            if account_id not in manager.active_connections:
                # Account disconnected, remove task
                self.remove_account_snapshot_task(account_id)
                return

            # Execute optimized snapshot update
            db: Session = SessionLocal()
            try:
                # Save latest prices for account's positions (less frequently)
                if start_time.second % 30 == 0:  # Only every 30 seconds
                    self._save_position_prices(db, account_id)

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Account {account_id} snapshot update failed: {e}")
        finally:
            execution_time = (datetime.now() - start_time).total_seconds()
            if execution_time > 5:  # Log if execution takes longer than 5 seconds
                logger.warning(f"Slow snapshot execution for account {account_id}: {execution_time:.2f}s")
    
    def _save_position_prices(self, db: Session, account_id: int):
        """
        Save latest prices for account's positions on the current date

        Args:
            db: Database session
            account_id: Account ID
        """
        try:
            # Get all account's positions
            positions = db.query(Position).filter(
                Position.account_id == account_id,
                Position.quantity > 0
            ).all()

            if not positions:
                logger.debug(f"Account {account_id} has no positions, skip price saving")
                return

            today = date.today()

            for position in positions:
                try:
                    # Check if crypto price already saved today
                    existing_price = db.query(CryptoPrice).filter(
                        CryptoPrice.symbol == position.symbol,
                        CryptoPrice.market == position.market,
                        CryptoPrice.price_date == today
                    ).first()

                    if existing_price:
                        logger.debug(f"crypto {position.symbol} price already exists for today, skip")
                        continue

                    # Get latest price
                    from services.market_data import get_last_price
                    current_price = get_last_price(position.symbol, position.market)

                    # Save price record
                    crypto_price = CryptoPrice(
                        symbol=position.symbol,
                        market=position.market,
                        price=current_price,
                        price_date=today
                    )

                    db.add(crypto_price)
                    db.commit()

                    logger.info(f"Saved crypto price: {position.symbol} {today} {current_price}")

                except Exception as e:
                    logger.error(f"Failed to save crypto {position.symbol} price: {e}")
                    db.rollback()
                    continue

        except Exception as e:
            logger.error(f"Failed to save account {account_id} position prices: {e}")
            db.rollback()
    
    def _create_database_snapshots(self):
        """
        Create database snapshots for all accounts
        Used for evaluation metrics calculation (volatility, drawdown, turnover, etc.)
        """
        db = SessionLocal()
        try:
            result = create_snapshots_for_all_accounts(db)
            logger.info(f"Database snapshot completed: {result['snapshots_created']} snapshots created")
        except Exception as e:
            logger.error(f"Database snapshot creation failed: {e}")
        finally:
            db.close()
    
    def _check_margin_levels(self):
        """
        Check margin levels for all accounts with leveraged positions
        Force close positions if margin falls below maintenance level
        """
        db = SessionLocal()
        try:
            # Get all accounts with leveraged positions
            accounts_with_positions = (
                db.query(Account)
                .join(Position, Position.account_id == Account.id)
                .filter(
                    Position.quantity > 0,
                    Position.leverage > 1
                )
                .distinct()
                .all()
            )
            
            if not accounts_with_positions:
                return
            
            for account in accounts_with_positions:
                try:
                    self._check_account_margin(db, account)
                except Exception as e:
                    logger.error(f"Error checking margin for account {account.name} (ID: {account.id}): {e}")
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Error in margin monitoring: {e}")
            db.rollback()
        finally:
            db.close()
    
    def _check_account_margin(self, db: Session, account: Account):
        """
        Check margin level for a specific account and liquidate if necessary
        
        Args:
            db: Database session
            account: Account to check
        """
        from services.market_data import get_last_price
        
        # Get all leveraged positions
        positions = db.query(Position).filter(
            Position.account_id == account.id,
            Position.quantity > 0,
            Position.leverage > 1
        ).all()
        
        if not positions:
            return
        
        # Calculate current equity (account value)
        total_position_value = Decimal(0)
        total_pnl = Decimal(0)
        
        for position in positions:
            try:
                # Get current market price
                current_price = get_last_price(position.symbol, position.market)
                if not current_price or current_price <= 0:
                    logger.warning(f"Invalid price for {position.symbol}, skipping margin check")
                    continue
                
                current_price = Decimal(str(current_price))
                quantity = Decimal(str(position.quantity))
                avg_cost = Decimal(str(position.avg_cost))
                
                # Calculate position value
                position_value = quantity * current_price
                
                # Calculate PnL based on position side
                if position.side == "LONG":
                    # Long: profit when price goes up
                    pnl = quantity * (current_price - avg_cost)
                elif position.side == "SHORT":
                    # Short: profit when price goes down
                    pnl = quantity * (avg_cost - current_price)
                else:
                    # Default to LONG if side not specified
                    pnl = quantity * (current_price - avg_cost)
                
                total_position_value += position_value
                total_pnl += pnl
                
            except Exception as e:
                logger.error(f"Error calculating PnL for {position.symbol}: {e}")
                continue
        
        # Calculate equity: cash + unrealized PnL
        equity = Decimal(str(account.current_cash)) + total_pnl
        margin_used = Decimal(str(account.margin_used))
        
        if margin_used <= 0:
            # No margin used, nothing to check
            return
        
        # Calculate margin level (equity / margin_used)
        margin_level = equity / margin_used
        maintenance_margin_ratio = Decimal(str(account.maintenance_margin_ratio))
        
        logger.debug(
            f"Account {account.name}: equity=${equity:.2f}, margin_used=${margin_used:.2f}, "
            f"margin_level={margin_level:.2%}, maintenance_required={maintenance_margin_ratio:.2%}"
        )
        
        # Check if margin level is below maintenance requirement
        if margin_level < maintenance_margin_ratio:
            logger.warning(
                f"⚠️ MARGIN CALL! Account {account.name} margin level {margin_level:.2%} "
                f"below maintenance {maintenance_margin_ratio:.2%}. Liquidating positions..."
            )
            
            # Force close all leveraged positions
            self._liquidate_positions(db, account, positions, reason="Insufficient margin")
    
    def _liquidate_positions(self, db: Session, account: Account, positions: List[Position], reason: str):
        """
        Force close (liquidate) all positions for an account
        
        Args:
            db: Database session
            account: Account being liquidated
            positions: List of positions to liquidate
            reason: Reason for liquidation
        """
        from services.order_matching import check_and_execute_order
        import uuid
        
        for position in positions:
            try:
                if float(position.quantity) <= 0:
                    continue
                
                # Determine close side: SELL closes LONG, BUY closes SHORT
                close_side = "SELL" if position.side == "LONG" else "BUY"
                
                # Create liquidation order
                order_no = f"LIQ-{uuid.uuid4().hex[:16].upper()}"
                
                order = Order(
                    account_id=account.id,
                    order_no=order_no,
                    symbol=position.symbol,
                    name=position.name,
                    market=position.market,
                    side=close_side,
                    order_type="MARKET",
                    price=None,  # Market order
                    quantity=float(position.quantity),
                    leverage=1,  # Closing orders don't use leverage
                    filled_quantity=0,
                    status="PENDING"
                )
                
                db.add(order)
                db.flush()
                
                # Execute immediately
                executed = check_and_execute_order(db, order)
                
                if executed:
                    logger.warning(
                        f"🔴 LIQUIDATED: {account.name} {close_side} {position.quantity} {position.symbol} "
                        f"at market price. Reason: {reason}"
                    )
                else:
                    logger.error(f"Failed to execute liquidation order {order_no} for {position.symbol}")
                    
            except Exception as e:
                logger.error(f"Error liquidating position {position.symbol} for {account.name}: {e}")


# Global scheduler instance
task_scheduler = TaskScheduler()


# Convenience functions
def start_scheduler():
    """Start global scheduler.

    This is the only sanctioned way to start the global scheduler; it is
    invoked by the runtime bootstrap's ``scheduler`` task descriptor.
    """
    task_scheduler.start()


def stop_scheduler():
    """Stop global scheduler; fail loudly when jobs could not be drained."""
    if not task_scheduler.shutdown():
        raise RuntimeError(
            "scheduler shutdown incomplete: running jobs did not stop within "
            "the shutdown timeout"
        )


def shutdown_cancellation_requested() -> bool:
    """Cooperative cancellation checkpoint for long-running scheduled jobs."""
    return task_scheduler.cancellation_requested()


def add_account_snapshot_job(account_id: int, interval_seconds: int = 10):
    """Add snapshot task for an account if the background runtime is enabled.

    Business entry points (e.g. WebSocket registration) may only request job
    updates from an already-running scheduler; when background tasks are
    disabled (NO_BACKGROUND) the request is skipped explicitly.
    """
    if not task_scheduler.is_running():
        logger.info(
            "Scheduler not running (background tasks disabled); "
            "skipping snapshot job for account %s",
            account_id,
        )
        return
    task_scheduler.add_account_snapshot_task(account_id, interval_seconds)


def remove_account_snapshot_job(account_id: int):
    """Convenience function to remove account snapshot task"""
    task_scheduler.remove_account_snapshot_task(account_id)


def start_margin_monitor(interval_seconds: int = 5):
    """Start margin monitoring task"""
    task_scheduler.add_margin_monitor_task(interval_seconds)
    logger.info(f"Margin monitor started - checking every {interval_seconds} seconds")


def start_database_snapshot(interval_seconds: int = 3600):
    """Start database snapshot task for all accounts"""
    task_scheduler.add_database_snapshot_task(interval_seconds)
    logger.info(f"Database snapshot started - creating snapshots every {interval_seconds} seconds")


# Legacy compatibility functions
def add_user_snapshot_job(user_id: int, interval_seconds: int = 10):
    """Legacy function - now redirects to account-based function"""
    # For backward compatibility, assume this is account_id
    add_account_snapshot_job(user_id, interval_seconds)


def remove_user_snapshot_job(user_id: int):
    """Legacy function - now redirects to account-based function"""
    # For backward compatibility, assume this is account_id
    remove_account_snapshot_job(user_id)


def setup_market_tasks():
    """Set up crypto market-related scheduled tasks"""
    from services.market_kline_service import refresh_market_klines, KLINE_REFRESH_INTERVAL_SECONDS
    from services.asset_curve_cache_service import (
        ASSET_CURVE_CACHE_REFRESH_SECONDS,
        refresh_asset_curve_cache_job,
    )

    # Prefetch and persist market kline data for cache usage
    task_scheduler.add_interval_task(
        task_func=refresh_market_klines,
        interval_seconds=KLINE_REFRESH_INTERVAL_SECONDS,
        task_id="market_kline_refresh"
    )
    logger.info(f"Market kline refresh scheduled every {KLINE_REFRESH_INTERVAL_SECONDS}s")

    # Persist asset curve points by timeframe to avoid on-request recomputation.
    warmup_time = datetime.now(timezone.utc) + timedelta(seconds=3)
    for timeframe, refresh_seconds in ASSET_CURVE_CACHE_REFRESH_SECONDS.items():
        task_scheduler.add_date_task(
            task_func=lambda tf=timeframe: refresh_asset_curve_cache_job(tf),
            run_date=warmup_time,
            task_id=f"asset_curve_cache_warmup_{timeframe}",
            misfire_grace_time=refresh_seconds,
        )
        task_scheduler.add_interval_task(
            task_func=lambda tf=timeframe: refresh_asset_curve_cache_job(tf),
            interval_seconds=refresh_seconds,
            task_id=f"asset_curve_cache_refresh_{timeframe}",
            start_date=warmup_time + timedelta(seconds=refresh_seconds),
        )
        logger.info(
            "Asset curve cache refresh scheduled: timeframe=%s interval=%ss",
            timeframe,
            refresh_seconds,
        )


def _ensure_market_data_ready() -> None:
    """Prefetch required market data before enabling trading tasks"""
    try:
        from services.trading_commands import AI_TRADING_SYMBOLS
        from services.market_data import get_last_price
        from services.market_kline_service import ensure_latest_kline

        missing_symbols: List[str] = []

        ensure_latest_kline([(symbol, "CRYPTO") for symbol in AI_TRADING_SYMBOLS])

        for symbol in AI_TRADING_SYMBOLS:
            try:
                price = get_last_price(symbol, "CRYPTO")
                if price is None or price <= 0:
                    missing_symbols.append(symbol)
                    logger.warning(f"Prefetch returned invalid price for {symbol}: {price}")
                else:
                    logger.debug(f"Prefetched market data for {symbol}: {price}")
            except Exception as fetch_err:
                missing_symbols.append(symbol)
                logger.warning(f"Failed to prefetch price for {symbol}: {fetch_err}")

        if missing_symbols:
            raise RuntimeError(
                "Market data not ready for symbols: " + ", ".join(sorted(set(missing_symbols)))
            )

    except Exception as err:
        logger.error(f"Market data readiness check failed: {err}")
        raise


def reset_auto_trading_job():
    """Reset the auto trading job after account configuration changes.

    Requires an already-running scheduler: the scheduler lifecycle is owned
    by the runtime bootstrap, so when background tasks are disabled
    (NO_BACKGROUND) this request is skipped explicitly instead of implicitly
    starting the global scheduler.
    """
    if not task_scheduler.is_running():
        logger.info(
            "Scheduler not running (background tasks disabled); "
            "skipping auto trading job reset"
        )
        return

    from services.trading_commands import (
        AI_TRADE_JOB_ID,
        BASELINE_TRADE_JOB_ID,
        place_ai_driven_crypto_order,
        place_baseline_driven_order,
    )
    import os

    def _parse_interval_env(env_name: str, default_value: int) -> int:
        raw_value = os.getenv(env_name)
        if raw_value is None or str(raw_value).strip() == "":
            return default_value
        try:
            parsed = int(str(raw_value).strip())
            if parsed <= 0:
                raise ValueError("must be > 0")
            return parsed
        except Exception:
            logger.warning(
                "Invalid %s=%r, fallback to default %s seconds",
                env_name,
                raw_value,
                default_value,
            )
            return default_value

    def _parse_required_first_execution_time(env_name: str) -> datetime:
        raw_value = os.getenv(env_name)
        if raw_value is None or str(raw_value).strip() == "":
            raise RuntimeError(
                f"Missing required {env_name}. "
                "Auto trading requires a fixed first execution time."
            )
        try:
            parsed = datetime.fromisoformat(str(raw_value).strip())
        except Exception as exc:
            raise RuntimeError(
                f"Invalid {env_name}={raw_value!r}; must be ISO datetime, e.g. 2026-01-01T08:00:00+08:00"
            ) from exc

        if parsed.tzinfo is None:
            raise RuntimeError(
                f"Invalid {env_name}={raw_value!r}; timezone offset is required, e.g. +08:00 or Z"
            )
        return parsed.astimezone(timezone.utc)

    # Define interval (defaults: AI 4h, baseline 5m; configurable via env)
    AI_TRADE_INTERVAL_SECONDS = _parse_interval_env("AI_TRADE_INTERVAL_SECONDS", 14400)
    BASELINE_TRADE_INTERVAL_SECONDS = _parse_interval_env("BASELINE_TRADE_INTERVAL_SECONDS", 300)
    FIRST_EXECUTION_TIME_UTC = _parse_required_first_execution_time("AI_TRADE_FIRST_EXECUTION_TIME")
    AI_TRADE_FIRST_JOB_ID = f"{AI_TRADE_JOB_ID}_first"
    BASELINE_TRADE_FIRST_JOB_ID = f"{BASELINE_TRADE_JOB_ID}_first"

    def _setup_jobs():
        try:
            # Ensure market data is ready before scheduling trading tasks
            # This can take time, so we do it in this background thread
            _ensure_market_data_ready()
            ai_first_run, ai_recurring_start = plan_first_and_recurring_runs(
                FIRST_EXECUTION_TIME_UTC,
                AI_TRADE_INTERVAL_SECONDS,
            )
            baseline_first_run, baseline_recurring_start = plan_first_and_recurring_runs(
                FIRST_EXECUTION_TIME_UTC,
                BASELINE_TRADE_INTERVAL_SECONDS,
            )
            ai_job = lambda: place_ai_driven_crypto_order(max_ratio=0.2)
            specs: list[JobSpec] = []
            if ai_first_run is not None:
                specs.append(JobSpec(
                    job_id=AI_TRADE_FIRST_JOB_ID,
                    func=ai_job,
                    trigger=DateTrigger(run_date=ai_first_run),
                    misfire_grace_time=AI_TRADE_INTERVAL_SECONDS,
                ))
            specs.append(JobSpec(
                job_id=AI_TRADE_JOB_ID,
                func=ai_job,
                trigger=IntervalTrigger(
                    seconds=AI_TRADE_INTERVAL_SECONDS,
                    start_date=ai_recurring_start,
                ),
            ))
            if baseline_first_run is not None:
                specs.append(JobSpec(
                    job_id=BASELINE_TRADE_FIRST_JOB_ID,
                    func=place_baseline_driven_order,
                    trigger=DateTrigger(run_date=baseline_first_run),
                    misfire_grace_time=BASELINE_TRADE_INTERVAL_SECONDS,
                ))
            specs.append(JobSpec(
                job_id=BASELINE_TRADE_JOB_ID,
                func=place_baseline_driven_order,
                trigger=IntervalTrigger(
                    seconds=BASELINE_TRADE_INTERVAL_SECONDS,
                    start_date=baseline_recurring_start,
                ),
            ))
            task_scheduler.reconcile_jobs("auto_trading", tuple(specs))

            # Log current jobs for verification
            jobs = task_scheduler.get_job_info()
            logger.info(
                "Auto trading jobs reset successfully - ai_interval=%ss baseline_interval=%ss first_execution_time_utc=%s ai_first_run=%s ai_recurring_start=%s baseline_first_run=%s baseline_recurring_start=%s; Jobs: %s",
                AI_TRADE_INTERVAL_SECONDS,
                BASELINE_TRADE_INTERVAL_SECONDS,
                FIRST_EXECUTION_TIME_UTC.isoformat(),
                ai_first_run.isoformat() if ai_first_run else None,
                ai_recurring_start.isoformat(),
                baseline_first_run.isoformat() if baseline_first_run else None,
                baseline_recurring_start.isoformat(),
                jobs,
            )

        except Exception as e:
            logger.error(f"Failed to reset auto trading jobs: {e}")
            raise

    # Runtime bootstrap already executes task startup in a worker thread. Do
    # not create an unowned daemon here: successful return is the point at
    # which TaskRegistry may truthfully mark this task RUNNING.
    _setup_jobs()
    logger.info("Completed auto trading job reset")


def stop_auto_trading_jobs() -> None:
    """Remove every job owned by the ai_auto_trading task descriptor."""
    if task_scheduler.is_running():
        task_scheduler.reconcile_jobs("auto_trading", ())


def get_ai_trade_schedule_status() -> Dict[str, Optional[str]]:
    """Return AI trade schedule status including next decision time in UTC."""
    from services.trading_commands import AI_TRADE_JOB_ID
    import os
    first_job_id = f"{AI_TRADE_JOB_ID}_first"

    def _parse_required_first_execution_time(env_name: str) -> datetime:
        raw_value = os.getenv(env_name)
        if raw_value is None or str(raw_value).strip() == "":
            raise RuntimeError(f"{env_name} is required but not set")
        try:
            parsed = datetime.fromisoformat(str(raw_value).strip())
        except Exception as exc:
            raise RuntimeError(
                f"Invalid {env_name}={raw_value!r}; must be ISO datetime, e.g. 2026-01-01T08:00:00+08:00"
            ) from exc
        if parsed.tzinfo is None:
            raise RuntimeError(
                f"Invalid {env_name}={raw_value!r}; timezone offset is required, e.g. +08:00 or Z"
            )
        return parsed.astimezone(timezone.utc)

    raw_interval = os.getenv("AI_TRADE_INTERVAL_SECONDS", "14400")
    first_execution_time_utc = _parse_required_first_execution_time("AI_TRADE_FIRST_EXECUTION_TIME")

    interval_seconds = int(str(raw_interval).strip())
    if interval_seconds <= 0:
        raise RuntimeError(f"AI_TRADE_INTERVAL_SECONDS must be > 0, got {interval_seconds}")

    next_run_dt: Optional[datetime] = None
    if task_scheduler.scheduler:
        candidate_times: List[datetime] = []
        first_job = task_scheduler.scheduler.get_job(first_job_id)
        recurring_job = task_scheduler.scheduler.get_job(AI_TRADE_JOB_ID)

        for job in (first_job, recurring_job):
            if job and job.next_run_time:
                run_time = job.next_run_time
                if run_time.tzinfo is None:
                    run_time = run_time.replace(tzinfo=timezone.utc)
                candidate_times.append(run_time)

        if candidate_times:
            next_run_dt = min(candidate_times)

    if next_run_dt is None:
        first_run, recurring_run = plan_first_and_recurring_runs(
            first_execution_time_utc,
            interval_seconds,
            now=datetime.now(timezone.utc),
        )
        next_run_dt = first_run or recurring_run

    return {
        "job_id": AI_TRADE_JOB_ID,
        "interval_seconds": str(interval_seconds),
        "first_execution_time": first_execution_time_utc.isoformat(),
        "next_decision_time_utc": next_run_dt.astimezone(timezone.utc).isoformat(),
    }
