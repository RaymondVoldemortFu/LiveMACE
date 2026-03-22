import requests


DOH_URL = "https://dns.google/resolve"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _has_records(domain: str, record_type: str) -> bool:
    response = requests.get(DOH_URL, params={"name": domain, "type": record_type}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    answers = payload.get("Answer") or []
    return len(answers) > 0


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")

    try:
        has_ns = _has_records(domain, "NS")
        has_soa = _has_records(domain, "SOA")
        has_a = _has_records(domain, "A")
    except requests.RequestException as exc:
        return _error(f"Domain availability check failed: {exc}")

    registered = has_ns or has_soa or has_a
    data = {
        "domain": domain,
        "available": not registered,
        "method": "dns",
        "signals": {"ns": has_ns, "soa": has_soa, "a": has_a},
        "warning": "DNS-based availability is heuristic and may be inaccurate.",
    }
    return {"status": "ok", "error": None, "data": data}
