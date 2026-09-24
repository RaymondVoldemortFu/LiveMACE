"""M18: App factory, bootstrap pipeline and background-task lifecycle.

Layout (per module task doc M18):

- ``app``: ``AppSettings`` + ``create_app`` — assembly only, no side effects.
- ``schema`` / ``seed`` / ``credentials``: idempotent bootstrap stages moved
  out of ``main.py``, each returning a report of what it did.
- ``runtime``: ``StartupMode``, ``BootstrapContext``, ``RuntimeHandle``,
  ``bootstrap_runtime`` / ``shutdown_runtime`` (async only for lifespan).
- ``tasks``: ``TaskDescriptor`` (id/start/stop/required/dependencies) and
  the idempotent ``TaskRegistry``; default table binds current job entry
  points with unchanged intervals.
"""

from benchmark.bootstrap.app import AppSettings, create_app
from benchmark.bootstrap.runtime import (
    BootstrapContext,
    RuntimeHandle,
    RuntimeBootstrapError,
    RuntimeShutdownError,
    StartupMode,
    bootstrap_runtime,
    bootstrap_runtime_sync,
    shutdown_runtime,
    shutdown_runtime_sync,
)
from benchmark.bootstrap.tasks import (
    TaskDescriptor,
    TaskRegistry,
    TaskState,
    default_task_descriptors,
)

__all__ = [
    "AppSettings",
    "create_app",
    "StartupMode",
    "BootstrapContext",
    "RuntimeHandle",
    "RuntimeBootstrapError",
    "RuntimeShutdownError",
    "bootstrap_runtime",
    "bootstrap_runtime_sync",
    "shutdown_runtime",
    "shutdown_runtime_sync",
    "TaskDescriptor",
    "TaskRegistry",
    "TaskState",
    "default_task_descriptors",
]
