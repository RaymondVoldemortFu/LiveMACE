import requests


DOH_URL = "https://dns.google/resolve"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain") or params.get("service")
    if not domain or not str(domain).strip():
        return _error("Missing required parameter: domain")
    name = str(domain).strip().rstrip(".")
    if not name.endswith("."):
        name = name + "."
    try:
        r = requests.get(DOH_URL, params={"name": name, "type": "SRV"}, timeout=15)
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"SRV lookup error: {e}")
    answers = j.get("Answer") or []
    records = []
    for a in answers:
        data_str = a.get("data", "")
        parts = data_str.strip().split()
        if len(parts) >= 4:
            priority = int(parts[0])
            weight = int(parts[1])
            port = int(parts[2])
            target = parts[3].rstrip(".")
            records.append({"priority": priority, "weight": weight, "port": port, "target": target})
    records.sort(key=lambda x: (x["priority"], -x["weight"]))
    data = {"domain": domain, "srv_records": records}
    return {"status": "ok", "error": None, "data": data}
