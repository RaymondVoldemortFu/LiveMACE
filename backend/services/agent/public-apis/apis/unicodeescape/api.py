import codecs


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("input")
    mode = (params.get("mode") or "encode").strip().lower()
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text)
    try:
        if mode == "decode":
            result = codecs.decode(s, "unicode_escape")
            data = {"input": s, "result": result, "mode": "decode"}
        else:
            result = codecs.encode(s, "unicode_escape").decode("ascii")
            data = {"input": s, "result": result, "mode": "encode"}
        return {"status": "ok", "error": None, "data": data}
    except Exception as e:
        return _error(str(e))
