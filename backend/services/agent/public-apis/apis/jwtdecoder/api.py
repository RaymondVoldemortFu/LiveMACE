import base64
import json


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _b64decode(part: str):
    padding = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + padding)


def run(params: dict) -> dict:
    params = params or {}
    token = params.get("token") or params.get("jwt")
    if not token or not isinstance(token, str):
        return _error("Missing required parameter: token")

    parts = token.split(".")
    if len(parts) < 2:
        return _error("Invalid JWT format")

    try:
        header = json.loads(_b64decode(parts[0]).decode("utf-8"))
        payload = json.loads(_b64decode(parts[1]).decode("utf-8"))
    except Exception:
        return _error("Invalid JWT encoding")

    data = {"header": header, "payload": payload, "signature": parts[2] if len(parts) > 2 else None}
    return {"status": "ok", "error": None, "data": data}
