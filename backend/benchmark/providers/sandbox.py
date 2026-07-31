"""Synchronous sandbox provider port."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from benchmark.contracts import JsonValue
from benchmark.contracts.common import _freeze_mapping, _require_non_empty

from .health import HealthStatus


@dataclass(frozen=True)
class SandboxLease:
    account_id: int
    container_id: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        _require_non_empty(self.container_id, "container_id")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))


@runtime_checkable
class SandboxPort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def lease(self, account_id: int) -> SandboxLease: ...
    def release(self, lease: SandboxLease) -> None: ...
    def healthcheck(self) -> HealthStatus: ...


__all__ = ["SandboxLease", "SandboxPort"]
