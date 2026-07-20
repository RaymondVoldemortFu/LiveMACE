"""Memory store adapters."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from benchmark.contracts import JsonValue
from benchmark.providers import HealthStatus, MemoryRecord, MemoryStorePort


class LegacyMemoryStoreAdapter(MemoryStorePort):
    id = "core.memory.legacy"
    version = "1.0.0"
    capabilities = ("memory.read", "memory.write")
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, store: Any) -> None:
        self._store = store

    def search(self, account_id: int, query: str, limit: int) -> Sequence[MemoryRecord]:
        rows = self._store.search(account_id=account_id, query=query, limit=limit)
        records = []
        for row in rows or []:
            if isinstance(row, MemoryRecord):
                records.append(row)
            elif isinstance(row, Mapping):
                records.append(
                    MemoryRecord(
                        id=str(row.get("id") or row.get("memory_id") or "unknown"),
                        content=str(row.get("content") or row.get("text") or ""),
                        metadata=row.get("metadata") or {},
                        score=row.get("score"),
                    )
                )
        return tuple(records)

    def add(self, account_id: int, content: str, metadata: Mapping[str, JsonValue]) -> str:
        return str(self._store.add(account_id=account_id, content=content, metadata=dict(metadata)))

    def delete_all(self, account_id: int) -> int:
        return int(self._store.delete_all(account_id=account_id))

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = ["LegacyMemoryStoreAdapter"]
