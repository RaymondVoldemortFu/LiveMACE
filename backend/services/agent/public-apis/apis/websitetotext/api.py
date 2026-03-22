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
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "\n", html, flags=re.I)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "\n", text, flags=re.I)
    text = re.sub(r"<nav[^>]*>[\s\S]*?</nav>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    data = {"url": url, "text": text, "length": len(text)}
    return {"status": "ok", "error": None, "data": data}
