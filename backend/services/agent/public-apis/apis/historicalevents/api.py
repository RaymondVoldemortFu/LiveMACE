import datetime as _dt

import requests


HISTORY_URL = "https://history.muffinlabs.com/date"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    month = params.get("month")
    day = params.get("day")

    if month is None or day is None:
        today = _dt.date.today()
        month = month or today.month
        day = day or today.day

    try:
        month = int(month)
        day = int(day)
    except (TypeError, ValueError):
        return _error("Invalid month/day")

    try:
        response = requests.get(f"{HISTORY_URL}/{month}/{day}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Historical events error: {exc}")
    except ValueError:
        return _error("Historical events service returned invalid JSON")

    data = {
        "date": payload.get("date"),
        "events": payload.get("data", {}).get("Events", []),
        "births": payload.get("data", {}).get("Births", []),
        "deaths": payload.get("data", {}).get("Deaths", []),
    }
    return {"status": "ok", "error": None, "data": data}
