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
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code,wind_speed_10m", "daily": "temperature_2m_max,temperature_2m_min,weather_code", "timezone": "auto", "forecast_days": 3},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    data = {"lat": lat, "lon": lon, "current": j.get("current"), "daily": j.get("daily")}
    return {"status": "ok", "error": None, "data": data}
