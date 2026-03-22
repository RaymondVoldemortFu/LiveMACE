import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    lat = params.get("lat")
    lon = params.get("lon")
    date = params.get("date", "")
    if lat is None or lon is None:
        return _error("Missing required parameters: lat, lon")
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return _error("Invalid lat/lon")
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/astronomy",
            params={"latitude": lat, "longitude": lon, "date": date or "today"},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        daily = j.get("daily", {})
        data = {
            "lat": lat,
            "lon": lon,
            "date": date or "today",
            "moon_phase": daily.get("moon_phase"),
            "moonrise": daily.get("moonrise"),
            "moonset": daily.get("moonset"),
            "altitude": None,
            "azimuth": None,
            "distance_km": None,
        }
        return {"status": "ok", "error": None, "data": data}
    except requests.RequestException as e:
        data = {"lat": lat, "lon": lon, "altitude": None, "azimuth": None, "distance_km": None, "warning": str(e)}
        return {"status": "ok", "error": None, "data": data}
