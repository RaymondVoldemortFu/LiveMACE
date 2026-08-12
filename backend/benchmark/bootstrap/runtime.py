"""Runtime bootstrap orchestration (M18).

Public interface (frozen at the Wave 1 review):

    async def bootstrap_runtime(context: BootstrapContext) -> RuntimeHandle
    async def shutdown_runtime(handle: RuntimeHandle) -> None

The async here only serves the FastAPI lifespan; every stage runs
synchronously in a worker thread. Agent scheduling stays fully
synchronous and is untouched by this module.

Stage order (fixed): schema -> seed -> credentials -> runtime tasks
(Redis ready -> extension catalog -> Docker -> scheduler jobs).
``SCHEMA_ONLY`` stops after schema; ``NO_BACKGROUND`` runs everything
except runtime tasks, so route tests need no Redis/Docker/scheduler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

import anyio

from benchmark.bootstrap.tasks import TaskDescriptor, TaskRegistry

logger = logging.getLogger(__name__)


class RuntimeShutdownError(RuntimeError):
    """Raised after all stop callbacks ran when one or more failed."""

    def __init__(self, failures: dict[str, str]) -> None:
        self.failures = dict(failures)
        super().__init__(
            "runtime shutdown failed for: " + ", ".join(sorted(self.failures))
        )


class StartupMode(str, Enum):
    """Production default is FULL; tests pass their mode explicitly —
    never inferred from hidden environment variables."""

    FULL = "full"
    SCHEMA_ONLY = "schema_only"
    NO_BACKGROUND = "no_background"


@dataclass
class BootstrapContext:
    """What to bootstrap and how; stages injectable for tests."""

    mode: StartupMode = StartupMode.FULL
    task_descriptors: Optional[List[TaskDescriptor]] = None
    schema_stage: Optional[Callable[[], object]] = None
    seed_stage: Optional[Callable[[], object]] = None
    credential_stage: Optional[Callable[[], object]] = None


@dataclass
class RuntimeHandle:
    """Result of a bootstrap; owns the task registry for shutdown."""

    mode: StartupMode
    registry: TaskRegistry
    reports: dict = field(default_factory=dict)

    def is_ready(self) -> bool:
        return self.registry.is_ready()

    def health(self) -> dict:
        return self.registry.health()


def _default_schema_stage():
    from benchmark.bootstrap.schema import run_schema_bootstrap

    return run_schema_bootstrap()


def _default_seed_stage():
    from benchmark.bootstrap.seed import run_seed_bootstrap

    return run_seed_bootstrap()


def _default_credential_stage():
    from benchmark.bootstrap.credentials import run_credential_bootstrap

    return run_credential_bootstrap()


def bootstrap_runtime_sync(context: BootstrapContext) -> RuntimeHandle:
    """Synchronous bootstrap core (also used directly by scripts/tests)."""
    schema_stage = context.schema_stage or _default_schema_stage
    seed_stage = context.seed_stage or _default_seed_stage
    credential_stage = context.credential_stage or _default_credential_stage

    registry = TaskRegistry()
    handle = RuntimeHandle(mode=context.mode, registry=registry)

    handle.reports["schema"] = schema_stage()
    if context.mode == StartupMode.SCHEMA_ONLY:
        return handle

    handle.reports["seed"] = seed_stage()
    handle.reports["credentials"] = credential_stage()
    if context.mode == StartupMode.NO_BACKGROUND:
        return handle

    descriptors = context.task_descriptors
    if descriptors is None:
        from benchmark.bootstrap.tasks import default_task_descriptors

        descriptors = default_task_descriptors()
    for descriptor in descriptors:
        registry.register(descriptor)
    try:
        registry.start_all()
    except Exception:
        # Partial-start failure: stop only what already started, then
        # surface the original error.
        logger.error("runtime bootstrap failed; stopping already-started tasks")
        registry.stop_all()
        raise
    logger.info("all runtime services initialized")
    return handle


def shutdown_runtime_sync(handle: RuntimeHandle) -> None:
    """Reverse-order, idempotent shutdown; stop failures are reported."""
    results = handle.registry.stop_all()
    failed = {task_id: err for task_id, err in results.items() if err}
    if failed:
        logger.error("shutdown completed with stop failures: %s", failed)
        raise RuntimeShutdownError(failed)
    else:
        logger.info("all runtime services shut down")


async def bootstrap_runtime(context: BootstrapContext) -> RuntimeHandle:
    return await anyio.to_thread.run_sync(bootstrap_runtime_sync, context)


async def shutdown_runtime(handle: RuntimeHandle) -> None:
    await anyio.to_thread.run_sync(shutdown_runtime_sync, handle)
