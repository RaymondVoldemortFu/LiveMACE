import requests


DOH_URL = "https://dns.google/resolve"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")
    domain = domain.strip().rstrip(".")
    try:
        r = requests.get(DOH_URL, params={"name": domain, "type": "MX"}, timeout=15)
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"MX lookup error: {e}")
    answers = j.get("Answer") or []
    records = []
    for a in answers:
        data_str = a.get("data", "")
        parts = data_str.strip().split(None, 1)
        priority = int(parts[0]) if parts and parts[0].isdigit() else 0
        exchange = parts[1].rstrip(".") if len(parts) > 1 else ""
        records.append({"priority": priority, "exchange": exchange})
    records.sort(key=lambda x: (x["priority"], x["exchange"]))
    data = {"domain": domain, "mx_records": records}
    return {"status": "ok", "error": None, "data": data}
