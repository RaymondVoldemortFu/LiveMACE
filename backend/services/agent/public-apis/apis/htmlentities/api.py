import html


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    mode = (params.get("mode") or "encode").lower()

    if text is None:
        return _error("Missing required parameter: text")

    if mode == "decode":
        result = html.unescape(str(text))
    else:
        result = html.escape(str(text))

    data = {"mode": mode, "result": result}
    return {"status": "ok", "error": None, "data": data}
