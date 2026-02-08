import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    lat = params.get("lat") or params.get("latitude")
    lon = params.get("lon") or params.get("longitude")
    if lat is None or lon is None:
        return _error("Missing required parameters: lat, lon")
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return _error("Invalid lat/lon")
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "public-apis-client"},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    addr = j.get("address") or {}
    data = {
        "lat": lat,
        "lon": lon,
        "display_name": j.get("display_name"),
        "city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality"),
        "state": addr.get("state"),
        "country": addr.get("country"),
        "country_code": addr.get("country_code"),
        "postcode": addr.get("postcode"),
        "raw": addr,
    }
    return {"status": "ok", "error": None, "data": data}
