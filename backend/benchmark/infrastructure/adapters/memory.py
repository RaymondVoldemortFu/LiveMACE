"""Memory store adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from benchmark.contracts import JsonValue, Market
from benchmark.providers import HealthStatus, MemoryRecord, MemoryStorePort, ProviderError
from benchmark.providers.runtime import (
    provider_failure,
    require_sync_result,
    run_health_probe,
)


class LegacyMemoryStoreAdapter(MemoryStorePort):
    id = "core.memory.legacy"
    version = "1.0.0"
    capabilities = ("memory.read", "memory.write")
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(self, store: Any, db: Session | None = None, trace_id: str | None = None) -> None:
        self._store = store
        self._db = db
        self._trace_id = trace_id

    def search(
        self,
        account_id: int | str,
        query: str,
        limit: int,
        *,
        market: Market,
    ) -> Sequence[MemoryRecord]:
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a non-empty string")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        market_value = _market_value(market)
        try:
            rows = require_sync_result(
                self._store.search(
                    account_id=str(account_id),
                    query=query,
                    limit=limit,
                    db=self._db,
                    market=market_value,
                ),
                provider_id=self.id,
                operation="search",
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "search", exc, retryable=True) from exc
        if rows is None:
            rows = ()
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise ProviderError(
                "Memory provider returned an invalid search result",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "search"},
            )
        records = []
        for row in rows:
            if isinstance(row, MemoryRecord):
                records.append(row)
            elif isinstance(row, Mapping):
                record_id = row.get("id") or row.get("memory_id")
                if record_id is None:
                    raise ProviderError(
                        "Memory search row has no id",
                        code="PROVIDER_RESULT_INVALID",
                        provider_id=self.id,
                        details={"operation": "search"},
                    )
                records.append(
                    MemoryRecord(
                        id=str(record_id),
                        content=str(row.get("content") or row.get("text") or ""),
                        metadata=row.get("metadata") or {},
                        score=_as_optional_float(row.get("similarity", row.get("score"))),
                        created_at=_as_optional_datetime(row.get("created_at")),
                    )
                )
            else:
                raise ProviderError(
                    "Memory search row has an unsupported type",
                    code="PROVIDER_RESULT_INVALID",
                    provider_id=self.id,
                    details={
                        "operation": "search",
                        "row_type": type(row).__name__,
                    },
                )
        return tuple(records)

    def add(
        self,
        account_id: int | str,
        content: str,
        metadata: Mapping[str, JsonValue],
        *,
        market: Market,
    ) -> str:
        if not isinstance(content, str) or not content:
            raise ValueError("content must be a non-empty string")
        market_value = _market_value(market)
        try:
            result = require_sync_result(
                self._store.add(
                    account_id=str(account_id),
                    content=content,
                    metadata=dict(metadata),
                    trace_id=self._trace_id,
                    db=self._db,
                    market=market_value,
                ),
                provider_id=self.id,
                operation="add",
            )
            memory_id = _extract_memory_id(result)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "add", exc) from exc
        if not memory_id:
            raise ProviderError(
                "Memory provider did not return an id",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "add"},
            )
        return memory_id

    def delete_all(self, account_id: int | str) -> int:
        try:
            result = require_sync_result(
                self._store.clear_account_memories(account_id=str(account_id)),
                provider_id=self.id,
                operation="delete_all",
            )
            if isinstance(result, bool) or not isinstance(result, int) or result < 0:
                raise ProviderError(
                    "Memory provider returned an invalid delete count",
                    code="PROVIDER_RESULT_INVALID",
                    provider_id=self.id,
                    details={"operation": "delete_all"},
                )
            return result
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "delete_all", exc) from exc

    def healthcheck(self) -> HealthStatus:
        def probe(timeout_seconds: float) -> bool | None:
            healthcheck = getattr(self._store, "healthcheck", None)
            if callable(healthcheck):
                result = healthcheck(timeout_seconds=timeout_seconds)
                if isinstance(result, HealthStatus):
                    return result.status == "ok"
                return result
            readiness_fields = [
                name
                for name in ("model", "client", "collection", "index")
                if hasattr(self._store, name)
            ]
            if not readiness_fields:
                return None
            return all(getattr(self._store, name) is not None for name in readiness_fields)

        return run_health_probe(self.id, probe)


def _market_value(market: Market | str) -> str:
    if not isinstance(market, Market):
        raise TypeError("market must be Market")
    return market.value


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
