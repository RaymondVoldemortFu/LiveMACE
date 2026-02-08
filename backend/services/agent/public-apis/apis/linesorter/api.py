def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    reverse = params.get("reverse", False)
    if text is None:
        return _error("Missing required parameter: text")

    lines = str(text).splitlines()
    lines = sorted(lines, reverse=bool(reverse))
    data = {"lines": lines, "text": "\n".join(lines)}
    return {"status": "ok", "error": None, "data": data}
