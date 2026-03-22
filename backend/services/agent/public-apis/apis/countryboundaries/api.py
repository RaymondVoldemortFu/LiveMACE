import json
from functools import lru_cache

import requests


GEOJSON_URL = "https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


@lru_cache(maxsize=1)
def _load_geojson() -> dict:
    response = requests.get(GEOJSON_URL, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or "features" not in data:
        raise ValueError("Invalid geojson")
    return data


def run(params: dict) -> dict:
    params = params or {}
    country = params.get("country")
    if not country or not isinstance(country, str):
        return _error("Missing required parameter: country")

    code = country.strip().upper()
    if len(code) != 2:
        return _error("Invalid country: must be a 2-letter ISO code")

    try:
        geojson = _load_geojson()
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        return _error(f"Country boundaries dataset error: {exc}")

    feature = None
    for item in geojson.get("features", []):
        props = item.get("properties") or {}
        if props.get("ISO_A2") == code:
            feature = item
            break

    if not feature:
        return _error("Country not found in boundaries dataset")

    return {"status": "ok", "error": None, "data": {"features": [feature]}}
