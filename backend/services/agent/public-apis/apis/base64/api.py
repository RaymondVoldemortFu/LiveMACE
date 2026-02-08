import base64


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    action = (params.get("action") or "encode").strip().lower()
    if action not in {"encode", "decode"}:
        return _error("Invalid action. Allowed: encode, decode")

    try:
        if action == "encode":
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            return {
                "status": "ok",
                "error": None,
                "data": {
                    "action": "encode",
                    "original": text,
                    "encoded": encoded,
                    "length": len(encoded),
                },
            }

        decoded = base64.b64decode(text.encode("ascii"), validate=True).decode("utf-8", errors="replace")
        return {
            "status": "ok",
            "error": None,
            "data": {
                "action": "decode",
                "original": text,
                "encoded": decoded,
                "length": len(decoded),
            },
        }
    except (ValueError, UnicodeError) as exc:
        return _error(f"Base64 {action} failed: {exc}")
