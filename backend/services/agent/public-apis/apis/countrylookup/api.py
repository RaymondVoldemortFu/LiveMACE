import requests


RESTCOUNTRIES_NAME = "https://restcountries.com/v3.1/name"
RESTCOUNTRIES_ALPHA = "https://restcountries.com/v3.1/alpha"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _fetch_country(country: str) -> list:
    if len(country) in {2, 3}:
        response = requests.get(
            f"{RESTCOUNTRIES_ALPHA}/{country}",
            params={"fields": "name,tld,cca2,ccn3,cca3,cioc,independent,status,capital,altSpellings,region,subregion,languages,latlng,landlocked,flag"},
            timeout=20,
        )
    else:
        response = requests.get(
            f"{RESTCOUNTRIES_NAME}/{country}",
            params={"fields": "name,tld,cca2,ccn3,cca3,cioc,independent,status,capital,altSpellings,region,subregion,languages,latlng,landlocked,flag"},
            timeout=20,
        )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else [payload]


def run(params: dict) -> dict:
    params = params or {}
    country = params.get("country")
    if not country or not isinstance(country, str):
        return _error("Missing required parameter: country")

    search = country.strip()
    if not search:
        return _error("Missing required parameter: country")

    try:
        countries = _fetch_country(search)
    except requests.RequestException as exc:
        return _error(f"Country lookup service error: {exc}")
    except ValueError:
        return _error("Country lookup service returned invalid JSON")

    return {"status": "ok", "error": None, "data": {"search": search, "countries": countries}}
