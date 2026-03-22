import requests


NASA_URL = "https://data.nasa.gov/resource/y77d-th95.json"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    limit = params.get("limit", 20)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _error("Invalid limit")

    try:
        response = requests.get(NASA_URL, params={"$limit": limit}, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Meteorites data error: {exc}")
    except ValueError:
        return _error("Meteorites service returned invalid JSON")

    data = {"count": len(payload), "meteorites": payload}
    return {"status": "ok", "error": None, "data": data}
