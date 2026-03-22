import requests


IP_API_URL = "http://ip-api.com/json/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    ip = params.get("ip") or ""

    try:
        response = requests.get(f"{IP_API_URL}{ip}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"IP lookup error: {exc}")
    except ValueError:
        return _error("IP lookup service returned invalid JSON")

    if payload.get("status") != "success":
        return _error(payload.get("message") or "IP lookup failed")

    data = {
        "ip": payload.get("query"),
        "country": payload.get("country"),
        "countryCode": payload.get("countryCode"),
        "region": payload.get("regionName"),
        "city": payload.get("city"),
        "zip": payload.get("zip"),
        "lat": payload.get("lat"),
        "lon": payload.get("lon"),
        "timezone": payload.get("timezone"),
        "isp": payload.get("isp"),
        "org": payload.get("org"),
        "as": payload.get("as"),
    }
    return {"status": "ok", "error": None, "data": data}
