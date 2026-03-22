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
            "https://api.sunrise-sunset.org/json",
            params={"lat": lat, "lng": lon, "date": date, "formatted": 0},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    if j.get("status") != "OK":
        return _error("Sunrise/sunset service failed")
    res = j.get("results", {})
    data = {
        "lat": lat,
        "lon": lon,
        "date": date,
        "sunrise": res.get("sunrise"),
        "sunset": res.get("sunset"),
        "solar_noon": res.get("solar_noon"),
        "day_length": res.get("day_length"),
    }
    return {"status": "ok", "error": None, "data": data}
