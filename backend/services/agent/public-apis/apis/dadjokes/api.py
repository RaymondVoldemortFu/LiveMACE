import requests


DADJOKE_URL = "https://icanhazdadjoke.com/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    try:
        response = requests.get(
            DADJOKE_URL,
            headers={"Accept": "application/json", "User-Agent": "apiverve-local"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Dad jokes service error: {exc}")
    except ValueError:
        return _error("Dad jokes service returned invalid JSON")

    joke = payload.get("joke")
    if not joke:
        return _error("Dad jokes service returned empty response")

    return {"status": "ok", "error": None, "data": {"joke": joke}}
