"""Provider-specific error type."""

from __future__ import annotations

from typing import Mapping

from benchmark.contracts import JsonValue
from benchmark.contracts.errors import ProviderError as ContractProviderError


class ProviderError(ContractProviderError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "PROVIDER_ERROR",
        retryable: bool = False,
        provider_id: str = "unknown",
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        safe_details = {"retryable": retryable, "provider_id": provider_id}
        if details:
            safe_details.update(details)
        super().__init__(message, code=code, details=safe_details)
        self.retryable = retryable
        self.provider_id = provider_id


__all__ = ["ProviderError"]
