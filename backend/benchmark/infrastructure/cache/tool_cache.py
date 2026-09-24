"""Tool cache port around the existing RedisToolCache."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, ContextManager, Iterator, Protocol


class ToolCachePort(Protocol):
    def get(self, namespace: str, args: dict[str, Any], *, round_id: str | None = None) -> object: ...
    def set(self, namespace: str, args: dict[str, Any], value: object, *, ttl_seconds: int | None = None, round_id: str | None = None) -> None: ...
    def acquire_lock(self, namespace: str, args: dict[str, Any], *, round_id: str | None = None) -> ContextManager[bool]: ...


class LegacyToolCacheAdapter(ToolCachePort):
    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def get(self, namespace: str, args: dict[str, Any], *, round_id: str | None = None) -> object:
        return self._cache.get_json(namespace, args, round_id=round_id)

    def set(self, namespace: str, args: dict[str, Any], value: object, *, ttl_seconds: int | None = None, round_id: str | None = None) -> None:
        self._cache.set_json(namespace, args, value, ttl_seconds=ttl_seconds, round_id=round_id)

    @contextmanager
    def acquire_lock(self, namespace: str, args: dict[str, Any], *, round_id: str | None = None) -> Iterator[bool]:
        with self._cache.acquire_lock(namespace, args, round_id=round_id) as locked:
            yield locked


__all__ = ["LegacyToolCacheAdapter", "ToolCachePort"]
