import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    pool = params.get("pool")
    count = params.get("count", 1)

    if pool is None:
        return _error("Missing required parameter: pool")
    if isinstance(pool, str):
        items = [p.strip() for p in pool.split(",") if p.strip()]
    elif isinstance(pool, list):
        items = pool
    else:
        return _error("Invalid pool")

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count")
    if count < 1:
        return _error("Invalid count")

    picks = random.sample(items, min(count, len(items)))
    data = {"picks": picks}
    return {"status": "ok", "error": None, "data": data}
