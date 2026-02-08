import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


DNS_URL = "https://dns.google/resolve"


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
            tags[key.strip().lower()] = value.strip()
    return tags


def _parse_mailto(value: str) -> dict:
    email = None
    domain = None
    if value and "mailto:" in value:
        email = value.split("mailto:", 1)[1].split(",")[0].strip()
        if "@" in email:
            domain = email.split("@", 1)[1]
    return {"email": email, "domain": domain, "valid": bool(email)}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")

    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")

    dmarc_host = f"_dmarc.{domain}"
    records = _lookup_txt(dmarc_host)
    dmarc_record = None
    for record in records:
        if "v=DMARC1" in record:
            dmarc_record = record
            break

    if not dmarc_record:
        data = {
            "host": domain,
            "dmarcHost": dmarc_host,
            "hasDmarc": False,
            "dmarc_record": None,
            "rua": {"email": None, "domain": None, "valid": False},
            "ruf": {"email": None, "domain": None, "valid": False},
            "v": None,
            "p": None,
            "valid": False,
        }
        return {"status": "ok", "error": None, "data": data}

    tags = _parse_tags(dmarc_record)
    rua = _parse_mailto(tags.get("rua"))
    ruf = _parse_mailto(tags.get("ruf"))
    policy = tags.get("p")
    valid = tags.get("v") == "DMARC1" and policy in {"none", "quarantine", "reject"}

    data = {
        "host": domain,
        "dmarcHost": dmarc_host,
        "hasDmarc": True,
        "dmarc_record": dmarc_record,
        "rua": rua,
        "ruf": ruf,
        "v": tags.get("v"),
        "p": policy,
        "valid": valid,
    }
    return {"status": "ok", "error": None, "data": data}
