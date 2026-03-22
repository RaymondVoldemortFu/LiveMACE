import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


WORD_RE = re.compile(r"[A-Za-z0-9']+")


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    words = WORD_RE.findall(text)
    hashtags = []
    for word in words:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", word)
        if cleaned:
            hashtags.append("#" + cleaned.lower())

    data = {"text": text, "hashtags": list(dict.fromkeys(hashtags))}
    return {"status": "ok", "error": None, "data": data}
