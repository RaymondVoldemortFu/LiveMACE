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
            params={"latitude": lat, "longitude": lon, "daily": "shortwave_radiation_sum", "timezone": "auto", "past_days": 0, "forecast_days": 1},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    daily = j.get("daily") or {}
    radiation = daily.get("shortwave_radiation_sum")
    if radiation:
        kwh = radiation[0] if isinstance(radiation, list) else radiation
    else:
        kwh = None
    data = {"lat": lat, "lon": lon, "shortwave_radiation_sum_kwh_m2": kwh, "note": "Daily sum from open-meteo"}
    return {"status": "ok", "error": None, "data": data}
