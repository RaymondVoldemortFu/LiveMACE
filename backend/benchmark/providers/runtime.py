"""Shared enforcement helpers for synchronous provider adapters."""

from __future__ import annotations

import threading
from inspect import isawaitable
from typing import Callable
from typing import TypeVar

from .health import HEALTHCHECK_TIMEOUT_SECONDS, HealthStatus
from .errors import ProviderError

T = TypeVar("T")


def require_sync_result(value: T, *, provider_id: str, operation: str) -> T:
    """Reject an awaitable instead of silently creating an event loop."""

    if not isawaitable(value):
        return value
    close = getattr(value, "close", None)
    if callable(close):
        close()
    raise ProviderError(
        "Provider returned an awaitable from a synchronous operation",
        code="ASYNC_PROVIDER_UNSUPPORTED",
        provider_id=provider_id,
        details={"operation": operation},
    )


def provider_failure(
    provider_id: str,
    operation: str,
    exc: Exception,
    *,
    retryable: bool = False,
) -> ProviderError:
    """Create a stable error without copying provider messages or credentials."""

    if isinstance(exc, ProviderError):
        return exc
    return ProviderError(
        f"Provider operation failed: {operation}",
        code="PROVIDER_OPERATION_FAILED",
        retryable=retryable,
        provider_id=provider_id,
        details={"operation": operation, "error_type": type(exc).__name__},
    )


# Single-flight guard: at most one live probe worker per provider. A probe
# that outlived its timeout keeps its slot until it actually finishes, so
# periodic polling of a hung dependency cannot accumulate daemon threads.
_probe_registry_lock = threading.Lock()
_active_probes: dict[str, threading.Event] = {}


def run_health_probe(
    provider_id: str,
    probe: Callable[[float], bool | None],
    *,
    timeout_seconds: float = HEALTHCHECK_TIMEOUT_SECONDS,
) -> HealthStatus:
    """Run a synchronous, read-only cooperative health probe with a bound.

    ``None`` means the dependency exposes no usable probe. The timeout is
    passed to the dependency *and* enforced by running the probe in a
    controlled daemon worker with a bounded wait: a probe that blocks past
    the timeout yields ``unavailable`` immediately instead of hanging the
    healthcheck. The abandoned worker thread cannot be preempted, but probes
    are single-flight per provider: while a previous worker is still running,
    no new thread is created and the provider is reported ``unavailable``.
    """

    outcome: dict[str, object] = {}
    done = threading.Event()

    def _invoke_probe() -> None:
        try:
            outcome["result"] = probe(timeout_seconds)
        except BaseException as exc:  # reported in the calling thread below
            outcome["error"] = exc
        finally:
            done.set()

    with _probe_registry_lock:
        previous = _active_probes.get(provider_id)
        if previous is not None and not previous.is_set():
            # The previous probe is still blocked past its own timeout; a
            # hung probe is itself evidence the dependency is unhealthy.
            return HealthStatus(
                "unavailable",
                provider_id,
                "Provider health probe is still running from a previous check",
                {"timeout_seconds": timeout_seconds, "probe_in_flight": True},
            )
        _active_probes[provider_id] = done
        worker = threading.Thread(
            target=_invoke_probe,
            name=f"healthcheck-{provider_id}",
            daemon=True,
        )
        worker.start()

    if not done.wait(timeout_seconds):
        return HealthStatus(
            "unavailable",
            provider_id,
            "Provider health probe exceeded its timeout",
            {"timeout_seconds": timeout_seconds},
        )

    error = outcome.get("error")
    if error is not None:
        if not isinstance(error, Exception):
            raise error  # KeyboardInterrupt/SystemExit and friends
        return HealthStatus(
            "unavailable",
            provider_id,
            "Provider health probe failed",
            {"error_type": type(error).__name__},
        )

    try:
        result = require_sync_result(
            outcome.get("result"),
            provider_id=provider_id,
            operation="healthcheck",
        )
    except ProviderError as exc:
        return HealthStatus(
            "unavailable",
            provider_id,
            "Provider health probe failed",
            {"error_type": type(exc).__name__},
        )
    if result is None:
        return HealthStatus(
            "degraded",
            provider_id,
            "Provider exposes no read-only health probe",
        )
    if not isinstance(result, bool):
        return HealthStatus(
            "unavailable",
            provider_id,
            "Provider health probe returned an invalid result",
            {"result_type": type(result).__name__},
        )
    return HealthStatus("ok" if result else "unavailable", provider_id)


__all__ = ["provider_failure", "require_sync_result", "run_health_probe"]
