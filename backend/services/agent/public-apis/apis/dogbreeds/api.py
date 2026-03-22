import requests


DOG_API_URL = "https://api.thedogapi.com/v1/breeds"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize(text: str) -> str:
    return text.strip().lower()


def run(params: dict) -> dict:
    params = params or {}
    breed = params.get("breed") or params.get("name")

    try:
        response = requests.get(DOG_API_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Dog breeds lookup error: {exc}")
    except ValueError:
        return _error("Dog breeds service returned invalid JSON")

    if not isinstance(payload, list):
        return _error("Dog breeds service returned invalid data")

    breeds = payload
    if breed and isinstance(breed, str):
        needle = _normalize(breed)
        breeds = [item for item in breeds if needle in _normalize(item.get("name", ""))]

    data = {
        "count": len(breeds),
        "breeds": [
            {
                "name": item.get("name"),
                "temperament": item.get("temperament"),
                "life_span": item.get("life_span"),
                "bred_for": item.get("bred_for"),
                "breed_group": item.get("breed_group"),
                "origin": item.get("origin"),
                "height": item.get("height"),
                "weight": item.get("weight"),
            }
            for item in breeds
        ],
    }
    return {"status": "ok", "error": None, "data": data}
