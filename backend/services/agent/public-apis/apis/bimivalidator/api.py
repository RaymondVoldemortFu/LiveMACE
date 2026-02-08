import os

import requests


APIVERVE_BASE_URL = os.getenv("APIVERVE_BASE_URL", "https://api.apiverve.com")
APIVERVE_API_KEY = os.getenv("APIVERVE_API_KEY")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")
    if not APIVERVE_API_KEY:
        return _error("Missing APIVERVE_API_KEY for bimivalidator")

    try:
        response = requests.get(
            f"{APIVERVE_BASE_URL}/v1/bimivalidator",
            headers={"x-api-key": APIVERVE_API_KEY},
            params={"domain": domain},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"BIMI validator service error: {exc}")
    except ValueError:
        return _error("BIMI validator service returned invalid JSON")

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return _error(payload.get("error") if isinstance(payload, dict) else "BIMI validator service failed")

    return {"status": "ok", "error": None, "data": payload.get("data")}
