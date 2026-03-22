import requests


METALS_URL = "https://api.metals.live/v1/spot/gold"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    try:
        response = requests.get(METALS_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Gold price lookup error: {exc}")
    except ValueError:
        return _error("Gold price service returned invalid JSON")

    price = None
    if isinstance(payload, list) and payload:
        item = payload[0]
        if isinstance(item, list) and len(item) >= 2:
            price = item[1]

    data = {"price_usd_per_ounce": price, "source": "metals.live"}
    return {"status": "ok", "error": None, "data": data}
