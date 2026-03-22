import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    ip = params.get("ip") or params.get("address")
    if not ip or not str(ip).strip():
        return _error("Missing required parameter: ip")
    ip = str(ip).strip()
    try:
        r = requests.get(
            "https://onionoo.torproject.org/summary",
            params={"search": ip},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    relays = j.get("relays") or []
    is_tor = any(ip in (r.get("a") or []) for r in relays)
    data = {"ip": ip, "is_tor_exit": is_tor, "relays_found": len(relays)}
    return {"status": "ok", "error": None, "data": data}
