from urllib.parse import quote, quote_plus, unquote, unquote_plus


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("string") or params.get("input")
    mode = (params.get("mode") or "encode").strip().lower()
    safe = params.get("safe") or ""
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text)
    try:
        if mode == "decode":
            result = unquote_plus(s)
            data = {"input": s, "result": result, "mode": "decode"}
        else:
            result = quote_plus(s, safe=str(safe))
            data = {"input": s, "result": result, "mode": "encode"}
        return {"status": "ok", "error": None, "data": data}
    except Exception as e:
        return _error(str(e))
