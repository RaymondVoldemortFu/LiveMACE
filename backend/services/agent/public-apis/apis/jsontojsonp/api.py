import json
import re


CALLBACK_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    data = params.get("data")
    callback = params.get("callback", "callback")

    if data is None:
        return _error("Missing required parameter: data")
    if not CALLBACK_RE.match(str(callback)):
        return _error("Invalid callback name")

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return _error("Invalid JSON data")

    jsonp = f"{callback}({json.dumps(data)});"
    return {"status": "ok", "error": None, "data": {"jsonp": jsonp}}
