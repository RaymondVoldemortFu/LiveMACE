import random
import string


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    length = params.get("length") or params.get("len") or 8
    charset = (params.get("charset") or params.get("type") or "alphanumeric").strip().lower()
    try:
        length = int(length)
        length = max(1, min(64, length))
    except (TypeError, ValueError):
        length = 8
    if charset in ("hex", "hexadecimal"):
        pool = string.hexdigits.upper()[:16]
    elif charset == "numeric":
        pool = string.digits
    elif charset == "alphabetic":
        pool = string.ascii_letters
    elif charset == "base58":
        pool = string.digits + "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    else:
        pool = string.ascii_letters + string.digits
    code = "".join(random.choices(pool, k=length))
    data = {"code": code, "length": length, "charset": charset}
    return {"status": "ok", "error": None, "data": data}
