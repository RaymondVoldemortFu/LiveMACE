import requests


ONWATER_URL = "https://api.onwater.io/api/v1/results"


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
        return _error("Invalid coordinates: lat and lon must be numeric")

    try:
        response = requests.get(f"{ONWATER_URL}/{lat},{lon}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Sea check service error: {exc}")
    except ValueError:
        return _error("Sea check service returned invalid JSON")

    is_sea = bool(payload.get("water"))
    return {
        "status": "ok",
        "error": None,
        "data": {"latitude": lat, "longitude": lon, "isSea": is_sea},
    }
