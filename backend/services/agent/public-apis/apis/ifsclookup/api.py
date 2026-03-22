import requests


IFSC_URL = "https://ifsc.razorpay.com/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    code = params.get("ifsc") or params.get("code")
    if not code or not isinstance(code, str):
        return _error("Missing required parameter: ifsc")

    try:
        response = requests.get(f"{IFSC_URL}{code}", timeout=15)
        if response.status_code == 404:
            return _error("IFSC not found")
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"IFSC lookup error: {exc}")
    except ValueError:
        return _error("IFSC service returned invalid JSON")

    return {"status": "ok", "error": None, "data": payload}
