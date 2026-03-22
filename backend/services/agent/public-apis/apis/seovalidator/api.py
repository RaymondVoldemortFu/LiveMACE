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
    title = (title.group(1).strip() if title else "").strip()
    meta_desc = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']', html, re.I)
    if not meta_desc:
        meta_desc = re.search(r'<meta\s+content=["\']([^"\']*)["\']\s+name=["\']description["\']', html, re.I)
    meta_desc = (meta_desc.group(1).strip() if meta_desc else "").strip()
    has_title = bool(title)
    title_len = len(title)
    desc_len = len(meta_desc)
    issues = []
    if not has_title:
        issues.append("Missing title tag")
    elif title_len > 60:
        issues.append("Title too long (recommend ≤60 chars)")
    if not meta_desc:
        issues.append("Missing meta description")
    elif desc_len > 160:
        issues.append("Meta description too long (recommend ≤160 chars)")
    data = {
        "url": url,
        "title": title,
        "title_length": title_len,
        "meta_description": meta_desc,
        "meta_description_length": desc_len,
        "valid": len(issues) == 0,
        "issues": issues,
    }
    return {"status": "ok", "error": None, "data": data}
