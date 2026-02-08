import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("name") or params.get("variable")
    case = (params.get("case") or params.get("style") or "camel").strip().lower()
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    s = str(text).strip()
    words = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip().split()
    words = [w for w in words if w]
    if not words:
        data = {"text": s, "result": s, "case": case}
        return {"status": "ok", "error": None, "data": data}
    if case == "snake":
        result = "_".join(w.lower() for w in words)
    elif case == "camel":
        result = words[0].lower() + "".join(w.capitalize() for w in words[1:])
    elif case == "pascal":
        result = "".join(w.capitalize() for w in words)
    elif case == "kebab":
        result = "-".join(w.lower() for w in words)
    else:
        result = "_".join(w.lower() for w in words)
    data = {"text": s, "result": result, "case": case}
    return {"status": "ok", "error": None, "data": data}
