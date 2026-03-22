import random
import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


WORD_RE = re.compile(r"[A-Za-z']+")


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    count = params.get("count", 3)

    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")
    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer")
    if count < 1:
        return _error("Invalid count: must be >= 1")

    words = WORD_RE.findall(text)
    if not words:
        return _error("No words found in text")

    targets = random.sample(words, min(count, len(set(words))))
    blanks = {word: "_" * len(word) for word in targets}

    def _replace(match):
        word = match.group(0)
        return blanks.get(word, word)

    masked = WORD_RE.sub(_replace, text)

    data = {
        "original": text,
        "blanked": masked,
        "answers": targets,
    }
    return {"status": "ok", "error": None, "data": data}
