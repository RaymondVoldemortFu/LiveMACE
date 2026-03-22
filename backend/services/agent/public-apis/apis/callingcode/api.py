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
            params={"fields": "name,idd,cca2"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Calling code service error: {exc}")
    except ValueError:
        return _error("Calling code service returned invalid JSON")

    if not payload:
        return _error("Country not found")

    data = payload[0] if isinstance(payload, list) else payload
    name = (data.get("name") or {}).get("common")
    official = (data.get("name") or {}).get("official")
    idd = data.get("idd") or {}
    root = idd.get("root") or ""
    suffixes = idd.get("suffixes") or []
    calling_codes = [f"{root}{suffix}" for suffix in suffixes] if root and suffixes else []

    return {
        "status": "ok",
        "error": None,
        "data": {
            "country": name,
            "officialName": official,
            "countryCode": data.get("cca2"),
            "callingcodes": calling_codes,
        },
    }
