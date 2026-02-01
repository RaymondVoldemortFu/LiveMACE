from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from config.system_time_config import SystemTimeConfig


def _delta_t_minutes() -> float:
    try:
        delta = float(SystemTimeConfig.DELTA_T_MINUTES)
    except (TypeError, ValueError):
        delta = 0.0
    if delta < 0:
        delta = 0.0
    return delta


def delta_t_minutes() -> float:
    return _delta_t_minutes()


def now_utc() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=_delta_t_minutes())


def now_timestamp() -> float:
    return now_utc().timestamp()


def now_timestamp_ms() -> int:
    return int(now_timestamp() * 1000)


def now_in_tz(tz: Optional[timezone]) -> datetime:
    if tz is None:
        return now_utc()
    return now_utc().astimezone(tz)
