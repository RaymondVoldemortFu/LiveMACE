import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


COMMON_SUBS = ["www", "mail", "ftp", "localhost", "api", "dev", "staging", "blog", "shop", "admin", "cdn", "static", "app", "m", "mobile"]


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not str(domain).strip():
        return _error("Missing required parameter: domain")
    domain = str(domain).strip().rstrip(".")
    found = []
    for sub in COMMON_SUBS:
        host = f"{sub}.{domain}"
        try:
            r = requests.get(f"https://{host}", timeout=3)
            found.append({"subdomain": host, "status": r.status_code})
        except requests.RequestException:
            try:
                r = requests.get(f"http://{host}", timeout=3)
                found.append({"subdomain": host, "status": r.status_code})
            except requests.RequestException:
                pass
    data = {"domain": domain, "subdomains": found, "count": len(found)}
    return {"status": "ok", "error": None, "data": data}
