import requests


GENDERIZE_URL = "https://api.genderize.io/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    name = params.get("name")
    if not name or not isinstance(name, str):
        return _error("Missing required parameter: name")

    try:
        response = requests.get(GENDERIZE_URL, params={"name": name}, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Gender detection error: {exc}")
    except ValueError:
        return _error("Gender detection service returned invalid JSON")

    data = {
        "name": payload.get("name"),
        "gender": payload.get("gender"),
        "probability": payload.get("probability"),
        "count": payload.get("count"),
        "source": "genderize.io",
    }
    return {"status": "ok", "error": None, "data": data}
