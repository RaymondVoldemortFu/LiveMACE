"""Memory store adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from benchmark.contracts import JsonValue, Market
from benchmark.providers import HealthStatus, MemoryRecord, MemoryStorePort


class LegacyMemoryStoreAdapter(MemoryStorePort):
    id = "core.memory.legacy"
    version = "1.0.0"
    capabilities = ("memory.read", "memory.write")
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, store: Any) -> None:
        self._store = store

    def search(
        self,
        account_id: int | str,
        query: str,
        limit: int,
        *,
        market: Market,
        db: Session | None = None,
    ) -> Sequence[MemoryRecord]:
        rows = self._store.search(
            account_id=str(account_id),
            query=query,
            limit=limit,
            db=db,
            market=market.value,
        )
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
                        score=_as_optional_float(row.get("similarity", row.get("score"))),
                        created_at=_as_optional_datetime(row.get("created_at")),
                    )
                )
        return tuple(records)

    def add(
        self,
        account_id: int | str,
        content: str,
        metadata: Mapping[str, JsonValue],
        *,
        market: Market,
        trace_id: str | None = None,
        db: Session | None = None,
    ) -> str:
        result = self._store.add(
            account_id=str(account_id),
            content=content,
            metadata=dict(metadata),
            trace_id=trace_id,
            db=db,
            market=market.value,
        )
        return _extract_memory_id(result)

    def delete_all(self, account_id: int | str) -> int:
        return int(self._store.clear_account_memories(account_id=str(account_id)))

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str):
        try:
            return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def _extract_memory_id(result: object) -> str:
    if isinstance(result, Mapping):
        for key in ("id", "memory_id"):
            value = result.get(key)
            if value is not None:
                return str(value)
        return ""
    return "" if result is None else str(result)


__all__ = ["LegacyMemoryStoreAdapter"]
