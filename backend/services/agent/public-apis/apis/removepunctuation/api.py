import re
import string


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text)
    punct = set(string.punctuation)
    removed = [c for c in s if c in punct]
    result = "".join(c for c in s if c not in punct)
    data = {"text": s, "result": result, "removed_count": len(removed), "removed_chars": "".join(sorted(set(removed)))}
    return {"status": "ok", "error": None, "data": data}
