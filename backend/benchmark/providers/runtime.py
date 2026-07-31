"""Shared enforcement helpers for synchronous provider adapters."""

from __future__ import annotations

from inspect import isawaitable
from typing import TypeVar

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


__all__ = ["provider_failure", "require_sync_result"]
