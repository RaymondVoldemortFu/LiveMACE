import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


DNS_URL = "https://dns.google/resolve"
SELECTOR_CANDIDATES = [
    "default",
    "selector1",
    "selector2",
    "google",
    "mail",
    "smtp",
    "dkim",
    "s1",
    "s2",
    "20230601",
    "20221208",
]


def _lookup_txt(name: str) -> list:
    try:
        response = requests.get(DNS_URL, params={"name": name, "type": "TXT"}, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return []
    except ValueError:
        return []

    answers = payload.get("Answer") or []
    records = []
    for item in answers:
        data = item.get("data")
        if data:
            records.append(data.strip('"'))
    return records


def _parse_tags(record: str) -> dict:
    tags = {}
    for part in record.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            tags[key.strip()] = value.strip()
    return tags


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    selector = params.get("selector")

    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")

    selectors = [str(selector)] if selector else SELECTOR_CANDIDATES
    found_selector = None
    dkim_record = None
    dkim_host = None
    for candidate in selectors:
        host = f"{candidate}._domainkey.{domain}"
        records = _lookup_txt(host)
        for record in records:
            if "v=DKIM1" in record:
                found_selector = candidate
                dkim_record = record
                dkim_host = host
                break
        if dkim_record:
            break

    if not dkim_record:
        data = {
            "selector": selector,
            "host": domain,
            "dkim_host": None,
            "cname_target": None,
            "has_dkim_record": False,
            "dkim_record": None,
            "dkim_records_count": 0,
            "version": None,
            "key_type": None,
            "issues_found": [],
            "valid": False,
        }
        return {"status": "ok", "error": None, "data": data}

    tags = _parse_tags(dkim_record)
    issues = []
    if "p" not in tags or not tags.get("p"):
        issues.append({"code": "MISSING_PUBLIC_KEY", "type": "error", "message": "Missing public key"})

    data = {
        "selector": found_selector,
        "host": domain,
        "dkim_host": dkim_host,
        "cname_target": None,
        "has_dkim_record": True,
        "dkim_record": dkim_record,
        "dkim_records_count": 1,
        "version": tags.get("v"),
        "key_type": tags.get("k"),
        "issues_found": issues,
        "valid": len(issues) == 0,
    }
    return {"status": "ok", "error": None, "data": data}
