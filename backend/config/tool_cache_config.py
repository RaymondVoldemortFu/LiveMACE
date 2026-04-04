import os


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class ToolCacheConfig:
    enabled = _to_bool(os.getenv("TOOL_CACHE_ENABLED"), default=True)
    redis_url = os.getenv("TOOL_CACHE_REDIS_URL", "redis://localhost:6379/0")
    key_prefix = os.getenv("TOOL_CACHE_KEY_PREFIX", "tool_cache")
    kline_ttl_seconds = int(os.getenv("TOOL_CACHE_KLINE_TTL_SECONDS", "900"))
    lock_timeout_seconds = int(os.getenv("TOOL_CACHE_LOCK_TIMEOUT_SECONDS", "15"))
    lock_wait_seconds = int(os.getenv("TOOL_CACHE_LOCK_WAIT_SECONDS", "5"))
    key_index_ttl_seconds = int(os.getenv("TOOL_CACHE_INDEX_TTL_SECONDS", "3600"))

