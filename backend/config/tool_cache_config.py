import os
import math


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(value: str, name: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return parsed


class ToolCacheConfig:
    enabled = _to_bool(os.getenv("TOOL_CACHE_ENABLED"), default=True)
    redis_url = os.getenv("TOOL_CACHE_REDIS_URL", "redis://localhost:6379/0")
    key_prefix = os.getenv("TOOL_CACHE_KEY_PREFIX", "tool_cache")
    kline_ttl_seconds = int(os.getenv("TOOL_CACHE_KLINE_TTL_SECONDS", "900"))
    lock_timeout_seconds = int(os.getenv("TOOL_CACHE_LOCK_TIMEOUT_SECONDS", "15"))
    lock_wait_seconds = int(os.getenv("TOOL_CACHE_LOCK_WAIT_SECONDS", "5"))
    key_index_ttl_seconds = int(os.getenv("TOOL_CACHE_INDEX_TTL_SECONDS", "3600"))
    socket_timeout_seconds = _positive_float(
        os.getenv("TOOL_CACHE_SOCKET_TIMEOUT_SECONDS", "2"),
        "TOOL_CACHE_SOCKET_TIMEOUT_SECONDS",
    )
    socket_connect_timeout_seconds = _positive_float(
        os.getenv("TOOL_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS", "2"),
        "TOOL_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS",
    )
