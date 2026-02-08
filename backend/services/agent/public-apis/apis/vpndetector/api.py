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
            "https://proxycheck.io/v2/" + ip,
            params={"vpn": "1", "asn": "0"},
            timeout=15,
        )
        if r.status_code != 200:
            data = {"ip": ip, "is_vpn": None, "note": "Service unavailable"}
            return {"status": "ok", "error": None, "data": data}
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    status = j.get(ip, {}) if isinstance(j, dict) else {}
    is_vpn = status.get("proxy") == "yes" or status.get("type") == "VPN"
    data = {"ip": ip, "is_vpn": is_vpn, "raw": status}
    return {"status": "ok", "error": None, "data": data}
