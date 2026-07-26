"""Sandbox adapter around ContainerService."""

from __future__ import annotations

from typing import Any, Mapping

from benchmark.contracts import JsonValue
from benchmark.providers import HealthStatus, SandboxLease, SandboxPort
from benchmark.providers.errors import ProviderError


class ContainerServiceSandboxAdapter(SandboxPort):
    id = "core.sandbox.container_service"
    version = "1.0.0"
    capabilities = ("sandbox.read", "sandbox.write")
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, container_service: Any) -> None:
        self._container_service = container_service

    def lease(self, account_id: int) -> SandboxLease:
        container_id = self._container_service.lease_container(account_id)
        if not container_id:
            raise ProviderError(
                "failed to lease sandbox container",
                code="SANDBOX_LEASE_FAILED",
                retryable=True,
                provider_id=self.id,
            )
        return SandboxLease(account_id=account_id, container_id=str(container_id))

    def release(self, lease: SandboxLease) -> None:
        release = getattr(self._container_service, "release_container", None)
        if callable(release):
            release(lease.account_id)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = ["ContainerServiceSandboxAdapter"]
