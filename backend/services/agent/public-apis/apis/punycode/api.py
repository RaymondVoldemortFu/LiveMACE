def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("domain") or params.get("input")
    mode = (params.get("mode") or "encode").strip().lower()
    if text is None or str(text).strip() == "":
        return _error("Missing required parameter: text or domain")
    raw = str(text).strip()
    try:
        if mode == "decode":
            output = raw.encode("ascii").decode("idna")
            data = {"input": raw, "output": output, "mode": "decode"}
        else:
            output = raw.encode("idna").decode("ascii")
            data = {"input": raw, "output": output, "mode": "encode"}
        return {"status": "ok", "error": None, "data": data}
    except Exception as e:
        return _error(str(e))
