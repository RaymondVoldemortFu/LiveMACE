def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    mode = (params.get("mode") or "characters").strip().lower()
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text)
    if mode == "words":
        result = " ".join(s.split()[::-1])
    elif mode == "lines":
        result = "\n".join(s.splitlines()[::-1])
    else:
        result = s[::-1]
    data = {"text": s, "result": result, "mode": mode}
    return {"status": "ok", "error": None, "data": data}
