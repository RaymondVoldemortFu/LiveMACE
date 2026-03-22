import re


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
REF_RE = re.compile(r"\[([^\]]+)\]:\s*(\S+)")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")

    inline = [{"text": m.group(1), "url": m.group(2)} for m in LINK_RE.finditer(text)]
    refs = [{"label": m.group(1), "url": m.group(2)} for m in REF_RE.finditer(text)]
    data = {"inline_links": inline, "reference_links": refs}
    return {"status": "ok", "error": None, "data": data}
