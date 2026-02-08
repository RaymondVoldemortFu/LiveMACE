import requests


ADVICE_URL = "https://api.adviceslip.com/advice"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    try:
        response = requests.get(ADVICE_URL, timeout=10, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Advice service error: {exc}")
    except ValueError:
        return _error("Advice service returned invalid JSON")

    slip = payload.get("slip", {})
    advice = slip.get("advice")
    if not advice:
        return _error("Advice service returned empty response")

    return {
        "status": "ok",
        "error": None,
        "data": {
            "id": str(slip.get("id", "")) or None,
            "advice": advice,
            "lang": "en",
        },
    }
