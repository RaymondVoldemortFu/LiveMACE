import math


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    n = params.get("n")
    r = params.get("r")
    try:
        n = int(n)
        r = int(r)
    except (TypeError, ValueError):
        return _error("n and r must be integers")
    if n < 0 or r < 0:
        return _error("n and r must be non-negative")
    if r > n:
        return _error("r cannot be greater than n")
    perm = math.factorial(n) // math.factorial(n - r) if r <= n else 0
    comb = math.factorial(n) // (math.factorial(r) * math.factorial(n - r)) if r <= n else 0
    data = {"n": n, "r": r, "permutation": perm, "combination": comb}
    return {"status": "ok", "error": None, "data": data}
