def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    dp = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        prev = dp[0]
        dp[0] = i
        for j, cb in enumerate(b, start=1):
            temp = dp[j]
            cost = 0 if ca == cb else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = temp
    return dp[-1]


def run(params: dict) -> dict:
    params = params or {}
    a = params.get("a")
    b = params.get("b")
    if a is None or b is None:
        return _error("Missing required parameters: a, b")

    distance = _lev(str(a), str(b))
    data = {"a": a, "b": b, "distance": distance}
    return {"status": "ok", "error": None, "data": data}
