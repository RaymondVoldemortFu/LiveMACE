def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def _format_size(num: float, decimals: int) -> str:
    idx = 0
    while num >= 1024 and idx < len(UNITS) - 1:
        num /= 1024.0
        idx += 1
    return f"{num:.{decimals}f} {UNITS[idx]}"


def run(params: dict) -> dict:
    params = params or {}
    value = params.get("bytes") or params.get("size")
    decimals = params.get("decimals", 2)

    try:
        num = float(value)
    except (TypeError, ValueError):
        return _error("Missing required parameter: bytes")
    try:
        decimals = int(decimals)
    except (TypeError, ValueError):
        return _error("Invalid decimals: must be an integer")

    data = {"bytes": num, "formatted": _format_size(num, decimals)}
    return {"status": "ok", "error": None, "data": data}
