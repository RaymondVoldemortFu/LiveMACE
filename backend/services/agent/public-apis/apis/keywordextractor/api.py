import re
from collections import Counter

import requests


WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _extract_text(url: str) -> str:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.text


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url")
    text = params.get("text")
    limit = params.get("limit", 10)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _error("Invalid limit")

    if url:
        try:
            text = _extract_text(url)
        except requests.RequestException as exc:
            return _error(f"Keyword extractor error: {exc}")

    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text or url")

    words = [w.lower() for w in WORD_RE.findall(text) if len(w) > 2]
    counts = Counter(words)
    keywords = [{"keyword": k, "count": v} for k, v in counts.most_common(limit)]

    data = {"count": len(keywords), "keywords": keywords}
    return {"status": "ok", "error": None, "data": data}
