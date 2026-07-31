"""Sandbox adapter around ContainerService."""

from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator, Mapping

from benchmark.contracts import JsonValue
from benchmark.providers import HealthStatus, SandboxLease, SandboxPort
from benchmark.providers.errors import ProviderError
from benchmark.providers.runtime import provider_failure, require_sync_result


class ContainerServiceSandboxAdapter(SandboxPort):
    id = "core.sandbox.container_service"
    version = "1.0.0"
    capabilities = ("sandbox.read", "sandbox.write")
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, container_service: Any) -> None:
        self._container_service = container_service
        self._released: set[tuple[int, str]] = set()
        self._lock = RLock()

    def lease(self, account_id: int) -> SandboxLease:
        try:
            container_id = require_sync_result(
                self._container_service.lease_container(account_id),
                provider_id=self.id,
                operation="lease",
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "lease", exc, retryable=True) from exc
        if not container_id:
            raise ProviderError(
                "failed to lease sandbox container",
                code="SANDBOX_LEASE_FAILED",
                retryable=True,
                provider_id=self.id,
            )
        return SandboxLease(account_id=account_id, container_id=str(container_id))

    def release(self, lease: SandboxLease) -> None:
        if not isinstance(lease, SandboxLease):
            raise TypeError("lease must be SandboxLease")
        key = (lease.account_id, lease.container_id)
        with self._lock:
            if key in self._released:
                return
            release = getattr(self._container_service, "release_container", None)
            if not callable(release):
                raise ProviderError(
                    "Container service does not support release",
                    code="SANDBOX_RELEASE_UNSUPPORTED",
                    provider_id=self.id,
                )
            try:
                require_sync_result(
                    release(lease.account_id),
                    provider_id=self.id,
                    operation="release",
                )
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                raise
            except ProviderError:
                raise
            except Exception as exc:
                raise provider_failure(self.id, "release", exc, retryable=True) from exc
            self._released.add(key)

    @contextmanager
    def managed_lease(self, account_id: int) -> Iterator[SandboxLease]:
        lease = self.lease(account_id)
        try:
            yield lease
        finally:
            self.release(lease)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = ["ContainerServiceSandboxAdapter"]
