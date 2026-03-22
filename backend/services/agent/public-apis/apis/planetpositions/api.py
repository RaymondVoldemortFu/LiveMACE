import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    lat = params.get("lat") or params.get("latitude")
    lon = params.get("lon") or params.get("longitude")
    date = params.get("date") or "today"
    if lat is None or lon is None:
        return _error("Missing required parameters: lat, lon")
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return _error("Invalid lat/lon")
    try:
        r = requests.get(
            "https://api.visibleplanets.dev/v3/",
            params={"latitude": lat, "longitude": lon},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        positions = j.get("data", [])
        data = {"lat": lat, "lon": lon, "positions": positions, "meta": j.get("meta")}
        return {"status": "ok", "error": None, "data": data}
    except requests.RequestException as e:
        return _error(str(e))
