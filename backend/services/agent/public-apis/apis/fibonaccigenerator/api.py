def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    n = params.get("n", params.get("count", 10))

    try:
        n = int(n)
    except (TypeError, ValueError):
        return _error("Invalid n: must be an integer")
    if n < 1 or n > 1000:
        return _error("Invalid n: must be between 1 and 1000")

    seq = []
    a, b = 0, 1
    for _ in range(n):
        seq.append(a)
        a, b = b, a + b

    data = {"count": n, "sequence": seq}
    return {"status": "ok", "error": None, "data": data}
