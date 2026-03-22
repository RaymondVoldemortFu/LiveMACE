import re

import requests


LINK_RE = re.compile(r'href=["\'](.*?)["\']', re.IGNORECASE)


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
        return _error(f"Link scraper error: {exc}")

    links = LINK_RE.findall(html)
    data = {"url": url, "count": len(links), "links": links}
    return {"status": "ok", "error": None, "data": data}
