def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    name = params.get("name")
    if not name or not isinstance(name, str):
        return _error("Missing required parameter: name")

    parts = [p for p in name.strip().split() if p]
    if not parts:
        return _error("Invalid name")

    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    middle = " ".join(parts[1:-1]) if len(parts) > 2 else ""

    data = {"first": first, "middle": middle or None, "last": last or None, "full": name.strip()}
    return {"status": "ok", "error": None, "data": data}
