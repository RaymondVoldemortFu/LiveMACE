"""Shared enforcement helpers for synchronous provider adapters."""

from __future__ import annotations

from inspect import isawaitable
from time import monotonic
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


def run_health_probe(
    provider_id: str,
    probe: Callable[[float], bool | None],
) -> HealthStatus:
    """Run a synchronous, read-only cooperative health probe.

    ``None`` means the dependency exposes no usable probe. The timeout is
    passed to the dependency and also checked after return; arbitrary Python
    code is not preempted.
    """

    started = monotonic()
    try:
        result = require_sync_result(
            probe(HEALTHCHECK_TIMEOUT_SECONDS),
            provider_id=provider_id,
            operation="healthcheck",
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception as exc:
        return HealthStatus(
            "unavailable",
            provider_id,
            "Provider health probe failed",
            {"error_type": type(exc).__name__},
        )
    elapsed = monotonic() - started
    if elapsed > HEALTHCHECK_TIMEOUT_SECONDS:
        return HealthStatus(
            "unavailable",
            provider_id,
            "Provider health probe exceeded its timeout",
            {"elapsed_seconds": elapsed},
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
