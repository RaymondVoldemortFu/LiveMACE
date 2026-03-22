import re
import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url")
    if not url or not str(url).strip():
        return _error("Missing required parameter: url")
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        html = r.text
    except requests.RequestException as e:
        return _error(str(e))
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = len(text.split())
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    if words < 1:
        score = 0
    else:
        score = 206.835 - 1.015 * (words / sentences) - 84.6 * (words / max(1, words))
        score = max(0, min(100, round(score, 2)))
    data = {"url": url, "readability_score": score, "word_count": words, "sentence_count": sentences, "excerpt": text[:500]}
    return {"status": "ok", "error": None, "data": data}
