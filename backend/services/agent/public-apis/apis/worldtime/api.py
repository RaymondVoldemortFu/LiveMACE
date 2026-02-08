import requests
from datetime import datetime, timezone


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    timezone = params.get("timezone") or params.get("tz") or params.get("zone")
    if not timezone or not str(timezone).strip():
        return _error("Missing required parameter: timezone (e.g. America/New_York)")
    tz = str(timezone).strip()
    try:
        r = requests.get(
            "https://worldtimeapi.org/api/timezone/" + tz,
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    data = {
        "timezone": j.get("timezone"),
        "datetime": j.get("datetime"),
        "utc_offset": j.get("utc_offset"),
        "day_of_week": j.get("day_of_week"),
        "unixtime": j.get("unixtime"),
    }
    return {"status": "ok", "error": None, "data": data}
