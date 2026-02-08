import os

import requests


APIVERVE_BASE_URL = os.getenv("APIVERVE_BASE_URL", "https://api.apiverve.com")
APIVERVE_API_KEY = os.getenv("APIVERVE_API_KEY")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    state = params.get("state")
    county = params.get("county")

    if not state or not county:
        return _error("Missing required parameters: state, county")
    if not APIVERVE_API_KEY:
        return _error("Missing APIVERVE_API_KEY for county data lookup")

    try:
        response = requests.get(
            f"{APIVERVE_BASE_URL}/v1/countydata",
            headers={"x-api-key": APIVERVE_API_KEY},
            params={"state": state, "county": county},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"County data service error: {exc}")
    except ValueError:
        return _error("County data service returned invalid JSON")

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return _error(payload.get("error") if isinstance(payload, dict) else "County data service failed")

    return {"status": "ok", "error": None, "data": payload.get("data")}
