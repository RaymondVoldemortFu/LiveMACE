import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url") or params.get("link")
    if not url or not str(url).strip():
        return _error("Missing required parameter: url")
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = requests.head(url, timeout=15, allow_redirects=True)
        final = r.url
        status = r.status_code
    except requests.RequestException:
        try:
            r = requests.get(url, timeout=15, allow_redirects=True)
            final = r.url
            status = r.status_code
        except requests.RequestException as e:
            return _error(str(e))
    data = {"url": url, "final_url": final, "status_code": status, "resolved": final != url}
    return {"status": "ok", "error": None, "data": data}
