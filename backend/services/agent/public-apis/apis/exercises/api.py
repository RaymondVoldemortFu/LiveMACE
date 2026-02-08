import requests


WGER_URL = "https://wger.de/api/v2/exercise/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    query = params.get("query")
    limit = params.get("limit", 50)

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _error("Invalid limit: must be an integer")

    try:
        response = requests.get(
            WGER_URL,
            params={"language": 2, "limit": limit},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Exercises lookup error: {exc}")
    except ValueError:
        return _error("Exercises service returned invalid JSON")

    results = payload.get("results") or []
    if query and isinstance(query, str):
        needle = query.strip().lower()
        results = [item for item in results if needle in (item.get("name") or "").lower()]

    data = {
        "count": len(results),
        "exercises": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "category": item.get("category"),
                "muscles": item.get("muscles"),
                "equipment": item.get("equipment"),
            }
            for item in results
        ],
    }
    return {"status": "ok", "error": None, "data": data}
