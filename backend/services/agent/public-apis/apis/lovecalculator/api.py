def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _score(a: str, b: str) -> int:
    combined = (a + b).lower()
    score = sum(ord(c) for c in combined if c.isalpha()) % 101
    return score


def run(params: dict) -> dict:
    params = params or {}
    name1 = params.get("name1")
    name2 = params.get("name2")
    if not name1 or not name2:
        return _error("Missing required parameters: name1, name2")

    score = _score(str(name1), str(name2))
    data = {"name1": name1, "name2": name2, "compatibility": score}
    return {"status": "ok", "error": None, "data": data}
