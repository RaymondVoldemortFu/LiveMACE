import requests


RESTCOUNTRIES_URL = "https://restcountries.com/v3.1/alpha"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    name = params.get("name")
    if not name or not isinstance(name, str):
        return _error("Missing required parameter: name")

    code = name.strip().upper()
    if len(code) != 2:
        return _error("Invalid name: must be a 2-letter ISO code")

    try:
        response = requests.get(
            f"{RESTCOUNTRIES_URL}/{code}",
            params={"fields": "name,cca2,languages"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Country languages service error: {exc}")
    except ValueError:
        return _error("Country languages service returned invalid JSON")

    data = payload[0] if isinstance(payload, list) and payload else payload
    if not data:
        return _error("Country not found")

    languages = list((data.get("languages") or {}).values())
    return {
        "status": "ok",
        "error": None,
        "data": {
            "country": data.get("cca2"),
            "name": (data.get("name") or {}).get("common"),
            "officialName": (data.get("name") or {}).get("official"),
            "officialLanguages": languages,
            "officialLanguageCount": len(languages),
        },
    }
