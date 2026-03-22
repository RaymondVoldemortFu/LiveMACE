import uuid


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count") or params.get("n") or 1
    version = params.get("version") or "4"
    try:
        count = int(count)
        count = max(1, min(100, count))
    except (TypeError, ValueError):
        count = 1
    uuids = []
    for _ in range(count):
        if str(version) == "4":
            uuids.append(str(uuid.uuid4()))
        else:
            uuids.append(str(uuid.uuid4()))
    data = {"uuids": uuids, "count": len(uuids)}
    return {"status": "ok", "error": None, "data": data}
