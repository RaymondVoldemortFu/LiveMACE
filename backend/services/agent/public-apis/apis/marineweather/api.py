import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    lat = params.get("lat")
    lon = params.get("lon")
    if lat is None or lon is None:
        return _error("Missing required parameters: lat, lon")
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return _error("Invalid lat/lon")

    url = "https://marine-api.open-meteo.com/v1/marine"
    try:
        response = requests.get(
            url,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "wave_height,wave_direction,wind_wave_height,sea_surface_temperature",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Marine weather error: {exc}")
    except ValueError:
        return _error("Marine weather service returned invalid JSON")

    data = {"lat": lat, "lon": lon, "hourly": payload.get("hourly")}
    return {"status": "ok", "error": None, "data": data}
import requests


MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    lat = params.get("lat")
    lon = params.get("lon")
    if lat is None or lon is None:
        return _error("Missing required parameters: lat, lon")

    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return _error("Invalid lat/lon")

    try:
        response = requests.get(
            MARINE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "wave_height,wind_wave_height,sea_surface_temperature",
                "current": "wave_height,wind_wave_height,sea_surface_temperature",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Marine weather error: {exc}")
    except ValueError:
        return _error("Marine weather service returned invalid JSON")

    data = {
        "lat": lat,
        "lon": lon,
        "current": payload.get("current"),
        "hourly": payload.get("hourly"),
        "timezone": payload.get("timezone"),
    }
    return {"status": "ok", "error": None, "data": data}
