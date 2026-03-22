import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    year = params.get("year")
    category = params.get("category")
    try:
        r = requests.get(
            "https://api.nobelprize.org/2.1/nobelPrizes",
            params={"nobelPrizeYear": year, "nobelPrizeCategory": category},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"Nobel API error: {e}")
    prizes = j.get("nobelPrizes", [])
    data = {"nobelPrizes": prizes, "count": len(prizes)}
    return {"status": "ok", "error": None, "data": data}
