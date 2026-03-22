import os
import random
import threading
import time


_LOCK = threading.Lock()
_COUNTER = random.randint(0, 1679615)


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _base36(num: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    sign = "-" if num < 0 else ""
    num = abs(num)
    out = []
    while num:
        num, rem = divmod(num, 36)
        out.append(alphabet[rem])
    return sign + "".join(reversed(out))


def _fingerprint() -> str:
    host = os.getenv("HOSTNAME", "host")
    pid = os.getpid()
    raw = f"{host}-{pid}"
    value = sum(ord(ch) for ch in raw) % (36**4)
    return _base36(value).rjust(4, "0")


def _random_block(size: int) -> str:
    return _base36(random.randint(0, 36**size - 1)).rjust(size, "0")


def _next_counter() -> str:
    global _COUNTER
    with _LOCK:
        _COUNTER = (_COUNTER + 1) % (36**4)
        return _base36(_COUNTER).rjust(4, "0")


def _cuid() -> str:
    ts = _base36(int(time.time() * 1000))
    return f"c{ts}{_next_counter()}{_fingerprint()}{_random_block(4)}"


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count", 1)
    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer")
    if count < 1 or count > 100:
        return _error("Invalid count: must be between 1 and 100")

    cuids = [_cuid() for _ in range(count)]
    data = {
        "cuids": cuids,
        "count": count,
        "format": "c + timestamp + counter + fingerprint + random",
        "collision_resistant": True,
        "sortable": True,
    }
    return {"status": "ok", "error": None, "data": data}
