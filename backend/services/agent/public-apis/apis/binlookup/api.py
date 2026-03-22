import requests


BINLIST_URL = "https://lookup.binlist.net"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _clean_bin(bin_value: str) -> str:
    return "".join(ch for ch in bin_value if ch.isdigit())


def run(params: dict) -> dict:
    params = params or {}
    bin_value = params.get("bin")
    if not bin_value or not isinstance(bin_value, str):
        return _error("Missing required parameter: bin")

    bin_value = _clean_bin(bin_value)
    if len(bin_value) < 6:
        return _error("Invalid bin: must contain at least 6 digits")

    try:
        response = requests.get(f"{BINLIST_URL}/{bin_value}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"BIN lookup service error: {exc}")
    except ValueError:
        return _error("BIN lookup service returned invalid JSON")

    country = payload.get("country") or {}
    bank = payload.get("bank") or {}

    data = {
        "bin": bin_value[:6],
        "brand": payload.get("scheme") or payload.get("brand"),
        "type": payload.get("type"),
        "category": payload.get("level"),
        "country": country.get("name"),
        "issuer": {
            "name": bank.get("name"),
            "country": bank.get("country"),
            "phone": bank.get("phone"),
            "website": bank.get("url"),
        },
        "location": {
            "latitude": country.get("latitude"),
            "longitude": country.get("longitude"),
            "alpha2": country.get("alpha2"),
            "alpha3": country.get("alpha3"),
        },
    }

    return {"status": "ok", "error": None, "data": data}
