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
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "daily": "sunrise,sunset", "timezone": "auto", "forecast_days": 1},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    daily = j.get("daily") or {}
    sunrise = daily.get("sunrise", [None])[0]
    sunset = daily.get("sunset", [None])[0]
    r2 = requests.get(
        "https://api.sunrise-sunset.org/json",
        params={"lat": lat, "lng": lon, "date": date, "formatted": 0},
        timeout=10,
    )
    solar_noon = None
    if r2.status_code == 200 and r2.json().get("status") == "OK":
        res = r2.json().get("results", {})
        solar_noon = res.get("solar_noon")
    data = {"lat": lat, "lon": lon, "date": date, "sunrise": sunrise, "sunset": sunset, "solar_noon": solar_noon}
    return {"status": "ok", "error": None, "data": data}
