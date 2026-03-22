import re
import unicodedata


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("input")
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text).strip()
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s).strip("-").lower()
    data = {"text": str(text), "slug": s}
    return {"status": "ok", "error": None, "data": data}
