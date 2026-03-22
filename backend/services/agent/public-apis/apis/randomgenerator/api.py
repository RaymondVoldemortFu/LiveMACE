import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    min_val = params.get("min") or params.get("min_value") or 0
    max_val = params.get("max") or params.get("max_value") or 100
    count = params.get("count") or params.get("n") or 1
    integers = params.get("integers", True)
    try:
        min_val = float(min_val)
        max_val = float(max_val)
        count = int(count)
        count = max(1, min(1000, count))
    except (TypeError, ValueError):
        return _error("Invalid min, max, or count")
    if min_val > max_val:
        min_val, max_val = max_val, min_val
    if integers if isinstance(integers, bool) else str(integers).lower() in ("1", "true", "yes"):
        values = [random.randint(int(min_val), int(max_val)) for _ in range(count)]
    else:
        values = [random.uniform(min_val, max_val) for _ in range(count)]
    data = {"min": min_val, "max": max_val, "count": count, "values": values}
    return {"status": "ok", "error": None, "data": data}
