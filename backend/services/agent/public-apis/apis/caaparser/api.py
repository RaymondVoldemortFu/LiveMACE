import os

import requests


APIVERVE_BASE_URL = os.getenv("APIVERVE_BASE_URL", "https://api.apiverve.com")
APIVERVE_API_KEY = os.getenv("APIVERVE_API_KEY")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    record = (params or {}).get("record")
    if not record or not isinstance(record, str):
        return _error("Missing required parameter: record")
    if not APIVERVE_API_KEY:
        return _error("Missing APIVERVE_API_KEY for CAA record parsing")

    try:
        response = requests.post(
            f"{APIVERVE_BASE_URL}/v1/caaparser",
            headers={"x-api-key": APIVERVE_API_KEY},
            json={"record": record},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"CAA record parsing service error: {exc}")
    except ValueError:
        return _error("CAA record parsing service returned invalid JSON")

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return _error(payload.get("error") if isinstance(payload, dict) else "CAA record parsing failed")

    return {"status": "ok", "error": None, "data": payload.get("data")}
