import requests


DOH_URL = "https://dns.google/resolve"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not str(domain).strip():
        return _error("Missing required parameter: domain")
    domain = str(domain).strip().rstrip(".")
    try:
        r = requests.get(DOH_URL, params={"name": domain, "type": "TXT"}, timeout=15)
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"SPF lookup error: {e}")
    answers = j.get("Answer") or []
    spf_records = []
    for a in answers:
        data_str = a.get("data", "").strip('"')
        if data_str.startswith("v=spf1"):
            spf_records.append(data_str)
    valid = len(spf_records) > 0
    data = {"domain": domain, "spf_records": spf_records, "valid": valid, "count": len(spf_records)}
    return {"status": "ok", "error": None, "data": data}
