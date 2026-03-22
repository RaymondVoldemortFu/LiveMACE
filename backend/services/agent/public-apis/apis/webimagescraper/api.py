import re
import requests
from urllib.parse import urljoin, urlparse


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url")
    limit = params.get("limit") or 50
    if not url or not str(url).strip():
        return _error("Missing required parameter: url")
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        limit = min(100, max(1, int(limit)))
    except (TypeError, ValueError):
        limit = 50
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        html = r.text
    except requests.RequestException as e:
        return _error(str(e))
    base = url.rsplit("/", 1)[0] + "/"
    imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
    imgs += re.findall(r"url\(['\"]?([^'\")]+)['\"]?\)", html)
    seen = set()
    out = []
    for src in imgs:
        src = src.strip()
        if not src or src.startswith("data:"):
            continue
        full = urljoin(url, src)
        if full not in seen:
            seen.add(full)
            out.append(full)
            if len(out) >= limit:
                break
    data = {"url": url, "images": out, "count": len(out)}
    return {"status": "ok", "error": None, "data": data}
