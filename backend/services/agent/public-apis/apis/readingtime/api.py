import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    wpm = params.get("wpm") or params.get("words_per_minute") or 200
    if text is None:
        return _error("Missing required parameter: text")
    try:
        wpm = int(wpm)
        wpm = max(1, min(1000, wpm))
    except (TypeError, ValueError):
        wpm = 200
    s = str(text).strip()
    words = len(re.findall(r"\S+", s))
    minutes = words / wpm if wpm else 0
    data = {"word_count": words, "wpm": wpm, "minutes": round(minutes, 2), "seconds": round(minutes * 60, 1)}
    return {"status": "ok", "error": None, "data": data}
