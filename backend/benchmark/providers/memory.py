"""Synchronous memory provider port."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, Sequence

from benchmark.contracts import JsonValue
from benchmark.contracts.common import _freeze_mapping, _require_aware, _require_non_empty

from .health import HealthStatus


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    score: float | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        if not isinstance(self.content, str):
            raise TypeError("content must be str")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))
        if self.created_at is not None:
            _require_aware(self.created_at, "created_at")


class MemoryStorePort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def search(self, account_id: int, query: str, limit: int) -> Sequence[MemoryRecord]: ...
    def add(self, account_id: int, content: str, metadata: Mapping[str, JsonValue]) -> str: ...
    def delete_all(self, account_id: int) -> int: ...
    def healthcheck(self) -> HealthStatus: ...


__all__ = ["MemoryRecord", "MemoryStorePort"]
