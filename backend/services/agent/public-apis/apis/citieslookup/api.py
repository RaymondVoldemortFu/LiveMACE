import requests


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    city = params.get("city")
    limit = params.get("limit", 1)

    if not city or not isinstance(city, str):
        return _error("Missing required parameter: city")

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _error("Invalid limit: must be an integer between 1 and 20")

    if limit < 1 or limit > 20:
        return _error("Invalid limit: must be between 1 and 20")

    try:
        response = requests.get(
            GEOCODING_URL,
            params={"name": city, "count": limit, "language": "en", "format": "json"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Cities lookup service error: {exc}")
    except ValueError:
        return _error("Cities lookup service returned invalid JSON")

    results = payload.get("results") or []
    found = []
    for item in results:
        found.append(
            {
                "name": item.get("name"),
                "altName": item.get("admin1"),
                "country": item.get("country_code"),
                "featureCode": item.get("feature_code"),
                "population": item.get("population"),
                "loc": {
                    "type": "Point",
                    "coordinates": [item.get("longitude"), item.get("latitude")],
                },
            }
        )

    return {
        "status": "ok",
        "error": None,
        "data": {"search": city, "foundCities": found},
    }
