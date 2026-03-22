import requests


CHUCK_NORRIS_URL = "https://api.chucknorris.io/jokes/random"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    try:
        response = requests.get(CHUCK_NORRIS_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Chuck Norris service error: {exc}")
    except ValueError:
        return _error("Chuck Norris service returned invalid JSON")

    joke = payload.get("value")
    if not joke:
        return _error("Chuck Norris service returned no joke")

    return {"status": "ok", "error": None, "data": {"joke": joke}}
