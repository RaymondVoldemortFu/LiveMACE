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
        title = re.search(r"<title[^>]*>([^<]+)</title>", r.text, re.I)
        title = title.group(1).strip() if title else None
    except requests.RequestException as e:
        return _error(str(e))
    data = {"url": url, "title": title}
    return {"status": "ok", "error": None, "data": data}
