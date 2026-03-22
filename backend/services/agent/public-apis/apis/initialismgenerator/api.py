import re


STOPWORDS = {"and", "or", "the", "of", "a", "an", "to", "for", "in", "on", "at", "by"}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    keep_stopwords = params.get("keep_stopwords", False)
    keep_stopwords = bool(keep_stopwords)

    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    words = re.findall(r"[A-Za-z0-9]+", text)
    initials = []
    for word in words:
        if not keep_stopwords and word.lower() in STOPWORDS:
            continue
        initials.append(word[0].upper())

    data = {"text": text, "initialism": "".join(initials), "words": words}
    return {"status": "ok", "error": None, "data": data}
