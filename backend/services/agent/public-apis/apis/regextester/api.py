import re
import time


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    pattern = params.get("pattern") or params.get("regex")
    text = params.get("text") or params.get("input") or ""
    operation = (params.get("operation") or "match").strip().lower()
    replacement = params.get("replacement") or ""
    if pattern is None or pattern == "":
        return _error("Missing required parameter: pattern")
    try:
        rx = re.compile(str(pattern))
    except re.error as e:
        return _error(f"Invalid regex: {e}")
    s = str(text)
    t0 = time.perf_counter()
    try:
        if operation == "replace":
            result = rx.sub(str(replacement), s)
            data = {"operation": "replace", "result": result, "matches": None}
        elif operation == "split":
            result = rx.split(s)
            data = {"operation": "split", "result": result, "matches": None}
        elif operation == "findall":
            matches = rx.findall(s)
            data = {"operation": "findall", "matches": matches, "result": matches}
        else:
            m = rx.search(s)
            if m:
                data = {"operation": "match", "match": m.group(0), "groups": m.groups(), "start": m.start(), "end": m.end()}
            else:
                data = {"operation": "match", "match": None, "groups": None, "start": None, "end": None}
    except Exception as e:
        return _error(str(e))
    elapsed = (time.perf_counter() - t0) * 1000
    data["pattern"] = pattern
    data["text"] = s
    data["elapsed_ms"] = round(elapsed, 3)
    return {"status": "ok", "error": None, "data": data}
