from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url")
    utm_params = params.get("utm_params") or params.get("utm") or {}
    action = (params.get("action") or "add").strip().lower()
    if url is None or not str(url).strip():
        return _error("Missing required parameter: url")
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    utm_keys = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"]
    if action == "strip" or action == "remove":
        for k in utm_keys:
            qs.pop(k, None)
        new_query = urlencode(qs, doseq=True)
        new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        data = {"url": url, "result": new_url, "utm_params": {}}
        return {"status": "ok", "error": None, "data": data}
    if isinstance(utm_params, dict):
        for k, v in utm_params.items():
            if k in utm_keys and v is not None:
                qs[k] = [str(v)]
    new_query = urlencode(qs, doseq=True)
    new_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    extracted = {k: qs[k][0] for k in utm_keys if k in qs}
    data = {"url": url, "result": new_url, "utm_params": extracted}
    return {"status": "ok", "error": None, "data": data}
