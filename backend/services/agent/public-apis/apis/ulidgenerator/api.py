import time
import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_ts(ts_ms: int) -> str:
    s = ""
    for _ in range(10):
        s = ENCODING[ts_ms % 32] + s
        ts_ms //= 32
    return s


def _encode_random() -> str:
    s = ""
    for _ in range(16):
        s += ENCODING[random.randint(0, 31)]
    return s


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count") or 1
    try:
        count = int(count)
        count = max(1, min(100, count))
    except (TypeError, ValueError):
        count = 1
    ulids = []
    for _ in range(count):
        ts_ms = int(time.time() * 1000)
        ulid = _encode_ts(ts_ms) + _encode_random()
        ulids.append(ulid)
    data = {"ulids": ulids, "count": len(ulids)}
    return {"status": "ok", "error": None, "data": data}
