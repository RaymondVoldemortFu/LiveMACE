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
        return _error(f"Fetch error: {e}")
    title = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    title = title.group(1).strip() if title else None
    meta_desc = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']', html, re.I)
    if not meta_desc:
        meta_desc = re.search(r'<meta\s+content=["\']([^"\']*)["\']\s+name=["\']description["\']', html, re.I)
    meta_desc = meta_desc.group(1).strip() if meta_desc else None
    h1s = re.findall(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    data = {"url": url, "title": title, "meta_description": meta_desc, "h1_tags": h1s[:10]}
    return {"status": "ok", "error": None, "data": data}
