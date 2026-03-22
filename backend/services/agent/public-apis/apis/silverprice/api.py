import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    currency = (params.get("currency") or "USD").strip().upper()
    try:
        r = requests.get(
            "https://api.metals.live/v1/spot/silver",
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"Silver price error: {e}")
    price_usd = None
    if isinstance(j, list) and len(j) > 0:
        price_usd = j[0].get("price")
    elif isinstance(j, dict):
        price_usd = j.get("price")
    if price_usd is None:
        return _error("Could not get silver price")
    try:
        price_usd = float(price_usd)
    except (TypeError, ValueError):
        return _error("Invalid price data")
    if currency != "USD":
        try:
            er = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
            if er.status_code == 200:
                rate = er.json().get("rates", {}).get(currency, 1)
                price_converted = round(price_usd * rate, 4)
            else:
                price_converted = price_usd
        except Exception:
            price_converted = price_usd
    else:
        price_converted = price_usd
    data = {"metal": "silver", "price_usd": round(price_usd, 4), "currency": currency, "price": round(price_converted, 4)}
    return {"status": "ok", "error": None, "data": data}
