"""Sandbox adapter around ContainerService."""

from __future__ import annotations

from typing import Any, Mapping

from benchmark.contracts import JsonValue
from benchmark.providers import HealthStatus, SandboxLease, SandboxPort


class ContainerServiceSandboxAdapter(SandboxPort):
    id = "core.sandbox.container_service"
    version = "1.0.0"
    capabilities = ("sandbox.read", "sandbox.write")
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, container_service: Any) -> None:
        self._container_service = container_service

    def lease(self, account_id: int) -> SandboxLease:
        container = self._container_service.get_or_create_container(account_id)
        container_id = str(getattr(container, "id", None) or getattr(container, "short_id", None) or account_id)
        return SandboxLease(account_id=account_id, container_id=container_id)

    def release(self, lease: SandboxLease) -> None:
        release = getattr(self._container_service, "release_container", None)
        if callable(release):
            release(lease.account_id)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = ["ContainerServiceSandboxAdapter"]
