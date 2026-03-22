import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url") or params.get("domain") or params.get("host")
    if not url or not str(url).strip():
        return _error("Missing required parameter: url or domain")
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    timeout = 10
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        up = r.status_code < 500
        status_code = r.status_code
    except requests.RequestException:
        try:
            r = requests.get(url, timeout=timeout, allow_redirects=True)
            up = r.status_code < 500
            status_code = r.status_code
        except requests.RequestException as e:
            data = {"url": url, "up": False, "status_code": None, "error": str(e)}
            return {"status": "ok", "error": None, "data": data}
    data = {"url": url, "up": up, "status_code": status_code}
    return {"status": "ok", "error": None, "data": data}
