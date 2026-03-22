import requests


RESTCOUNTRIES_BASE = "https://restcountries.com/v3.1"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize_country_name(country: str) -> str:
    return country.strip()


def _extract_currencies(country: dict) -> list:
    currencies = country.get("currencies") or {}
    results = []
    for code, info in currencies.items():
        results.append(
            {
                "country_name": (country.get("name") or {}).get("common"),
                "currency_name": info.get("name"),
                "currency_iso_code": code,
                "currency_number": None,
                "currency_mnr_unts": None,
                "currency_symbol": info.get("symbol"),
            }
        )
    return results


def run(params: dict) -> dict:
    params = params or {}
    currency = params.get("currency")
    country = params.get("country")

    if not currency and not country:
        return _error("Missing required parameter: currency or country")

    if currency:
        code = str(currency).upper()
        try:
            response = requests.get(
                f"{RESTCOUNTRIES_BASE}/currency/{code}",
                params={"fields": "name,currencies"},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            return _error(f"Currency symbols service error: {exc}")
        except ValueError:
            return _error("Currency symbols service returned invalid JSON")

        countries_found = []
        for item in payload if isinstance(payload, list) else [payload]:
            countries_found.extend(_extract_currencies(item))

        data = {"countriesFound": countries_found, "currency": code}
        return {"status": "ok", "error": None, "data": data}

    search = _normalize_country_name(str(country))
    try:
        response = requests.get(
            f"{RESTCOUNTRIES_BASE}/name/{search}",
            params={"fields": "name,currencies"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Currency symbols service error: {exc}")
    except ValueError:
        return _error("Currency symbols service returned invalid JSON")

    countries_found = []
    for item in payload if isinstance(payload, list) else [payload]:
        countries_found.extend(_extract_currencies(item))

    data = {"countriesFound": countries_found, "country": search}
    return {"status": "ok", "error": None, "data": data}
