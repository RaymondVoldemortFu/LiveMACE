import os

import requests


APIVERVE_BASE_URL = os.getenv("APIVERVE_BASE_URL", "https://api.apiverve.com")
APIVERVE_API_KEY = os.getenv("APIVERVE_API_KEY")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    city = (params or {}).get("city")
    if not city or not isinstance(city, str):
        return _error("Missing required parameter: city")
    if not APIVERVE_API_KEY:
        return _error("Missing APIVERVE_API_KEY for air quality lookup")

    try:
        response = requests.get(
            f"{APIVERVE_BASE_URL}/v1/airquality",
            headers={"x-api-key": APIVERVE_API_KEY},
            params={"city": city},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Air quality service error: {exc}")
    except ValueError:
        return _error("Air quality service returned invalid JSON")

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return _error(payload.get("error") if isinstance(payload, dict) else "Air quality service failed")

    return {"status": "ok", "error": None, "data": payload.get("data")}
