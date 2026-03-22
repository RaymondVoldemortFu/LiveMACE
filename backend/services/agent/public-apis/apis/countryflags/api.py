import requests


RESTCOUNTRIES_URL = "https://restcountries.com/v3.1/alpha"
FLAGCDN_PNG = "https://flagcdn.com/w320"
FLAGCDN_SVG = "https://flagcdn.com"
CIRCLE_FLAGS = "https://hatscripts.github.io/circle-flags/flags"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    country = params.get("country")
    fmt = str(params.get("format", "png")).lower()
    shape = str(params.get("shape", "square")).lower()

    if not country or not isinstance(country, str):
        return _error("Missing required parameter: country")

    country = country.strip().upper()
    if len(country) != 2:
        return _error("Invalid country: must be a 2-letter ISO code")

    if fmt not in {"png", "svg"}:
        return _error("Invalid format: must be png or svg")
    if shape not in {"circle", "square"}:
        return _error("Invalid shape: must be circle or square")

    try:
        response = requests.get(
            f"{RESTCOUNTRIES_URL}/{country}",
            params={"fields": "name,cca2"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Country flags service error: {exc}")
    except ValueError:
        return _error("Country flags service returned invalid JSON")

    data = payload[0] if isinstance(payload, list) and payload else payload
    if not data:
        return _error("Country not found")

    code = data.get("cca2", country).lower()
    if shape == "circle":
        download_url = f"{CIRCLE_FLAGS}/{code}.{fmt}"
    else:
        download_url = f"{FLAGCDN_PNG}/{code}.png" if fmt == "png" else f"{FLAGCDN_SVG}/{code}.svg"

    return {
        "status": "ok",
        "error": None,
        "data": {
            "country": (data.get("name") or {}).get("common"),
            "countryCode": data.get("cca2"),
            "shape": shape,
            "format": fmt,
            "downloadUrl": download_url,
        },
    }
