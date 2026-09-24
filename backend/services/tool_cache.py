import contextvars
import hashlib
import json
import logging
import math
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from config.tool_cache_config import ToolCacheConfig

logger = logging.getLogger("tool_cache")


def _reject_json_constant(name: str) -> float:
    raise ValueError(f"non-standard JSON constant {name!r} is not allowed")


def _parse_finite_float(text: str) -> float:
    result = float(text)
    if not math.isfinite(result):
        raise ValueError(f"non-finite JSON number {text!r} is not allowed")
    return result

_current_round_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tool_cache_round_id",
    default=None,
)


class RedisToolCache:
    def __init__(self) -> None:
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(ToolCacheConfig.enabled)

    def ensure_ready(self) -> None:
        """
        Ensure Redis tool cache is strictly available.
        This system must not run without tool cache.
        """
        if not self.enabled:
            raise RuntimeError(
                "TOOL_CACHE_ENABLED is false, but tool-cache is mandatory. "
                "Please enable TOOL_CACHE and configure Redis in .env."
            )
        _ = self._get_client()

    def create_round_id(self, scope: str = "decision") -> str:
        scope_clean = str(scope or "decision").strip().replace(" ", "_")
        return f"{scope_clean}:{uuid.uuid4().hex}"

    def get_current_round_id(self) -> Optional[str]:
        return _current_round_id.get()

    @contextmanager
    def use_round(self, round_id: Optional[str]) -> Iterator[None]:
        token = _current_round_id.set(round_id)
        try:
            yield
        finally:
            _current_round_id.reset(token)

    def _get_client(self):
        if not self.enabled:
            raise RuntimeError(
                "TOOL_CACHE_ENABLED is false, but tool-cache is required. "
                "Set TOOL_CACHE_ENABLED=true and provide TOOL_CACHE_REDIS_URL."
            )
        if self._client is not None:
            return self._client

        try:
            import redis

            self._client = redis.Redis.from_url(
                ToolCacheConfig.redis_url,
                decode_responses=True,
                socket_timeout=ToolCacheConfig.socket_timeout_seconds,
                socket_connect_timeout=ToolCacheConfig.socket_connect_timeout_seconds,
            )
            self._client.ping()
            logger.info(f"Redis tool cache connected: {ToolCacheConfig.redis_url}")
            return self._client
        except Exception as e:
            self._client = None
            raise RuntimeError(
                "Redis tool cache initialization failed. "
                "Please verify TOOL_CACHE_REDIS_URL and Redis service availability."
            ) from e

    @staticmethod
    def _stable_json(value: Dict[str, Any]) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )

    def _build_data_key(self, tool_name: str, args: Dict[str, Any], round_id: str) -> str:
        payload = self._stable_json({"tool": tool_name, "args": args, "round_id": round_id})
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{ToolCacheConfig.key_prefix}:round:{round_id}:tool:{tool_name}:{digest}"

    def _build_round_index_key(self, round_id: str) -> str:
        return f"{ToolCacheConfig.key_prefix}:round:{round_id}:keys"

    def _build_round_stats_key(self, round_id: str) -> str:
        return f"{ToolCacheConfig.key_prefix}:round:{round_id}:stats"

    def _increment_round_stat(self, client, round_id: str, field: str) -> None:
        if not round_id:
            return
        stats_key = self._build_round_stats_key(round_id)
        try:
            client.hincrby(stats_key, field, 1)
            client.expire(stats_key, ToolCacheConfig.key_index_ttl_seconds)
        except Exception:
            # Stats must never affect the main cache path.
            pass

    def get_json(
        self,
        tool_name: str,
        args: Dict[str, Any],
        round_id: Optional[str] = None,
        suppress_miss_log: bool = False,
    ) -> Optional[Any]:
        effective_round_id = round_id or self.get_current_round_id()
        if not effective_round_id:
            return None

        key = self._build_data_key(tool_name, args, effective_round_id)
        try:
            client = self._get_client()
            raw = client.get(key)
            if raw is None:
                self._increment_round_stat(client, effective_round_id, "miss")
                if not suppress_miss_log:
                    logger.debug(f"Tool cache miss: tool={tool_name} round={effective_round_id}")
                return None
            value = json.loads(
                raw,
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_float,
            )
            self._increment_round_stat(client, effective_round_id, "hit")
            logger.debug(f"Tool cache hit: tool={tool_name} round={effective_round_id}")
            return value
        except Exception as e:
            logger.warning(f"Tool cache read failed (tool={tool_name}): {e}")
            return None

    def set_json(
        self,
        tool_name: str,
        args: Dict[str, Any],
        value: Any,
        ttl_seconds: Optional[int] = None,
        round_id: Optional[str] = None,
    ) -> bool:
        effective_round_id = round_id or self.get_current_round_id()
        if not effective_round_id:
            return False

        key = self._build_data_key(tool_name, args, effective_round_id)
        index_key = self._build_round_index_key(effective_round_id)
        try:
            client = self._get_client()
            serialized = json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)
            ttl = max(
                1,
                int(
                    ttl_seconds
                    if ttl_seconds is not None
                    else ToolCacheConfig.kline_ttl_seconds
                ),
            )
            set_value = getattr(client, "set", None)
            if callable(set_value):
                set_value(key, serialized, ex=ttl)
            else:
                client.setex(key, ttl, serialized)
            client.sadd(index_key, key)
            client.expire(index_key, max(ttl, ToolCacheConfig.key_index_ttl_seconds))
            self._increment_round_stat(client, effective_round_id, "set")
            logger.debug(f"Tool cache set: tool={tool_name} round={effective_round_id} ttl={ttl}s")
            return True
        except Exception as e:
            logger.warning(f"Tool cache write failed (tool={tool_name}): {e}")
            return False

    @contextmanager
    def acquire_lock(
        self,
        tool_name: str,
        args: Dict[str, Any],
        round_id: Optional[str] = None,
    ) -> Iterator[bool]:
        effective_round_id = round_id or self.get_current_round_id()
        if not effective_round_id:
            yield False
            return

        data_key = self._build_data_key(tool_name, args, effective_round_id)
        lock_key = f"{data_key}:lock"
        lock = None
        acquired = False
        try:
            client = self._get_client()
            lock = client.lock(
                name=lock_key,
                timeout=max(1, int(ToolCacheConfig.lock_timeout_seconds)),
                blocking_timeout=max(1, int(ToolCacheConfig.lock_wait_seconds)),
            )
            acquired = bool(lock.acquire(blocking=True))
        except Exception as e:
            logger.warning(f"Tool cache lock failed (tool={tool_name}): {e}")
            acquired = False
        try:
            yield acquired
        finally:
            if acquired and lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass

    def clear_round(self, round_id: Optional[str]) -> int:
        if not round_id:
            return 0

        index_key = self._build_round_index_key(round_id)
        stats_key = self._build_round_stats_key(round_id)
        try:
            client = self._get_client()
            keys = list(client.smembers(index_key) or [])
            deleted = 0
            if keys:
                deleted += int(client.delete(*keys) or 0)
            deleted += int(client.delete(index_key) or 0)
            raw_stats = client.hgetall(stats_key) or {}
            hits = int(raw_stats.get("hit", 0) or 0)
            misses = int(raw_stats.get("miss", 0) or 0)
            sets = int(raw_stats.get("set", 0) or 0)
            total_reads = hits + misses
            hit_rate = (hits / total_reads * 100.0) if total_reads > 0 else 0.0
            deleted += int(client.delete(stats_key) or 0)
            logger.info(
                "Tool cache round cleared: round=%s deleted_keys=%s hit=%s miss=%s set=%s hit_rate=%.2f%%",
                round_id,
                deleted,
                hits,
                misses,
                sets,
                hit_rate,
            )
            return deleted
        except Exception as e:
            logger.warning(f"Failed to clear tool cache round {round_id}: {e}")
            return 0


tool_cache = RedisToolCache()
