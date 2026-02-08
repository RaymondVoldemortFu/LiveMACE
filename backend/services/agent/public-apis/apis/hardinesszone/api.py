import requests


PHZM_URL = "https://phzmapi.org"


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
        response = requests.get(f"{PHZM_URL}/{lat},{lon}.json", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Hardiness zone lookup error: {exc}")
    except ValueError:
        return _error("Hardiness zone service returned invalid JSON")

    data = {
        "lat": lat,
        "lon": lon,
        "zone": payload.get("zone"),
        "temperature_range": payload.get("temperature_range"),
    }
    return {"status": "ok", "error": None, "data": data}
