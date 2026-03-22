import re

import requests


TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_RE = re.compile(r'<meta\s+[^>]*name=["\']([^"\']+)["\'][^>]*content=["\']([^"\']+)["\']', re.IGNORECASE)


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url")
    if not url or not isinstance(url, str):
        return _error("Missing required parameter: url")

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        html = response.text
    except requests.RequestException as exc:
        return _error(f"Metadata extractor error: {exc}")

    title_match = TITLE_RE.search(html)
    title = title_match.group(1).strip() if title_match else None
    metas = {m.group(1).lower(): m.group(2) for m in META_RE.finditer(html)}

    data = {"title": title, "meta": metas}
    return {"status": "ok", "error": None, "data": data}
