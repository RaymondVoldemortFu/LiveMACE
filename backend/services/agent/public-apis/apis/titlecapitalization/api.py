import re

# Articles, conjunctions, short prepositions (APA/Chicago style): lowercase unless first/last word
LOWER = {"a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet", "as", "at", "by", "in", "of", "on", "to", "up", "via", "with"}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("title")
    style = (params.get("style") or "title").strip().lower()
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text).strip()
    if not s:
        data = {"text": s, "result": s}
        return {"status": "ok", "error": None, "data": data}
    if style == "sentence":
        result = s[0].upper() + s[1:].lower() if len(s) > 1 else s.upper()
    else:
        words = re.split(r"(\s+)", s)
        result = []
        for i, w in enumerate(words):
            if not w.strip():
                result.append(w)
                continue
            low = w.lower()
            if low in LOWER and i > 0 and i < len(words) - 1:
                result.append(low)
            else:
                result.append(w[0].upper() + w[1:].lower() if len(w) > 1 else w.upper())
        result = "".join(result)
    data = {"text": s, "result": result, "style": style}
    return {"status": "ok", "error": None, "data": data}
