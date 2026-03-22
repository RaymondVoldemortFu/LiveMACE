import os

import requests


APIVERVE_BASE_URL = os.getenv("APIVERVE_BASE_URL", "https://api.apiverve.com")
APIVERVE_API_KEY = os.getenv("APIVERVE_API_KEY")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    make = params.get("make")
    year = params.get("year")
    model = params.get("model")
    trim = params.get("trim")

    if make is None and year is None and model is None:
        return _error("Missing required parameter: make or year")
    if not APIVERVE_API_KEY:
        return _error("Missing APIVERVE_API_KEY for car models lookup")

    try:
        response = requests.get(
            f"{APIVERVE_BASE_URL}/v1/carmodels",
            headers={"x-api-key": APIVERVE_API_KEY},
            params={"make": make, "year": year, "model": model, "trim": trim},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Car models service error: {exc}")
    except ValueError:
        return _error("Car models service returned invalid JSON")

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return _error(payload.get("error") if isinstance(payload, dict) else "Car models service failed")

    return {"status": "ok", "error": None, "data": payload.get("data")}
