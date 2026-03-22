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
            "https://api.sunrise-sunset.org/json",
            params={"lat": lat, "lng": lon, "date": date or "today", "formatted": 0},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("status") != "OK":
            return _error("Moon rise/set service failed")
        res = j.get("results", {})
        try:
            rm = requests.get(
                "https://api.open-meteo.com/v1/astronomy",
                params={"latitude": lat, "longitude": lon, "date": date or "today"},
                timeout=10,
            )
            moon_rise, moon_set = None, None
            if rm.status_code == 200:
                dm = rm.json().get("daily", {})
                moon_rise, moon_set = dm.get("moonrise"), dm.get("moonset")
        except Exception:
            moon_rise, moon_set = None, None
        data = {
            "lat": lat,
            "lon": lon,
            "date": date or "today",
            "sunrise": res.get("sunrise"),
            "sunset": res.get("sunset"),
            "moonrise": moon_rise,
            "moonset": moon_set,
            "solar_noon": res.get("solar_noon"),
            "day_length": res.get("day_length"),
        }
        return {"status": "ok", "error": None, "data": data}
    except requests.RequestException as e:
        return _error(f"Moon rise/set error: {e}")
