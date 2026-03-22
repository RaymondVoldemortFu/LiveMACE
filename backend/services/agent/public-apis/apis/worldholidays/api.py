import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    country = params.get("country") or params.get("code") or "US"
    year = params.get("year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if not year:
        from datetime import datetime
        year = datetime.now().year
    try:
        r = requests.get(
            "https://date.nager.at/api/v3/PublicHolidays/" + str(year) + "/" + str(country).strip()[:2],
            timeout=15,
        )
        if r.status_code != 200:
            return _error("Holidays API unavailable or invalid country")
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    data = {"country": country, "year": year, "holidays": j}
    return {"status": "ok", "error": None, "data": data}
