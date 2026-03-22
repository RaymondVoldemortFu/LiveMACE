import requests


RESTCOUNTRIES_URL = "https://restcountries.com/v3.1/alpha"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    country = params.get("country")
    if not country or not isinstance(country, str):
        return _error("Missing required parameter: country")

    country = country.strip().upper()
    if len(country) != 2:
        return _error("Invalid country: must be a 2-letter ISO code")

    try:
        response = requests.get(
            f"{RESTCOUNTRIES_URL}/{country}",
            params={"fields": "name,cca2,borders,region,subregion,latlng,landlocked"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Country borders service error: {exc}")
    except ValueError:
        return _error("Country borders service returned invalid JSON")

    data = payload[0] if isinstance(payload, list) and payload else payload
    if not data:
        return _error("Country not found")

    latlng = data.get("latlng") or []
    coords = {"lat": latlng[0], "lng": latlng[1]} if len(latlng) >= 2 else {"lat": None, "lng": None}
    borders = data.get("borders") or []

    return {
        "status": "ok",
        "error": None,
        "data": {
            "country": (data.get("name") or {}).get("common"),
            "cca2": data.get("cca2"),
            "landlocked": data.get("landlocked"),
            "region": data.get("region"),
            "subregion": data.get("subregion"),
            "coordinates": coords,
            "borders": borders,
        },
    }
