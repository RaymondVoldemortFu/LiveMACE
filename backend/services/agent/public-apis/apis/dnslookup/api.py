import requests


DOH_URL = "https://dns.google/resolve"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _query_record(name: str, record_type: str) -> list:
    response = requests.get(
        DOH_URL,
        params={"name": name, "type": record_type},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    answers = payload.get("Answer") or []
    results = []
    for answer in answers:
        results.append(
            {
                "name": answer.get("name"),
                "type": answer.get("type"),
                "ttl": answer.get("TTL"),
                "data": answer.get("data"),
            }
        )
    return results


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain") or params.get("host")
    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")

    record_types = ["A", "AAAA", "MX", "TXT", "CNAME", "NS", "SOA", "SRV"]
    records = {}
    warnings = []
    try:
        for record_type in record_types:
            try:
                records[record_type] = _query_record(domain, record_type)
            except requests.RequestException as exc:
                warnings.append(f"{record_type} lookup failed: {exc}")
                records[record_type] = []
    except requests.RequestException as exc:
        return _error(f"DNS lookup error: {exc}")

    data = {
        "domain": domain,
        "records": records,
        "warnings": warnings or None,
    }
    return {"status": "ok", "error": None, "data": data}
