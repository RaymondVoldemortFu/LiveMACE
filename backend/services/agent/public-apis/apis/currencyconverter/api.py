import requests


RATES_URL = "https://open.er-api.com/v6/latest"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    value = params.get("value")
    from_code = params.get("from")
    to_code = params.get("to")

    if value is None:
        return _error("Missing required parameter: value")
    if not from_code or not to_code:
        return _error("Missing required parameters: from, to")

    try:
        value = float(value)
    except (TypeError, ValueError):
        return _error("Invalid value: must be a number")

    from_code = str(from_code).upper()
    to_code = str(to_code).upper()

    try:
        response = requests.get(f"{RATES_URL}/{from_code}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Currency rate service error: {exc}")
    except ValueError:
        return _error("Currency rate service returned invalid JSON")

    if payload.get("result") != "success":
        return _error(payload.get("error-type", "Currency rate service failed"))

    rates = payload.get("rates") or {}
    rate = rates.get(to_code)
    if rate is None:
        return _error("Unsupported target currency")

    converted = value * float(rate)
    data = {
        "from": from_code,
        "to": to_code,
        "value": value,
        "convertedValue": converted,
    }
    return {"status": "ok", "error": None, "data": data}
