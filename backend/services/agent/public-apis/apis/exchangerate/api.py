import requests


BASE_URL = "https://open.er-api.com/v6/latest/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    base = (params.get("base") or "USD").upper()
    target = params.get("target")

    try:
        response = requests.get(f"{BASE_URL}{base}", timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Exchange rate error: {exc}")
    except ValueError:
        return _error("Exchange rate service returned invalid JSON")

    rates = payload.get("rates") or {}
    if not isinstance(rates, dict):
        return _error("Exchange rate service returned invalid data")

    data = {"base": base, "rates": rates}
    if target:
        target = str(target).upper()
        data["target"] = target
        data["rate"] = rates.get(target)

    return {"status": "ok", "error": None, "data": data}
