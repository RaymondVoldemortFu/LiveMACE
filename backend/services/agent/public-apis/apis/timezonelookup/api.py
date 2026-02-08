from zoneinfo import ZoneInfo, available_timezones
from datetime import datetime, timezone


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    query = params.get("timezone") or params.get("tz") or params.get("query")
    if query is None or not str(query).strip():
        data = {"timezones": sorted(available_timezones())[:100], "count": 100}
        return {"status": "ok", "error": None, "data": data}
    name = str(query).strip()
    try:
        tz = ZoneInfo(name)
    except Exception:
        matches = [z for z in available_timezones() if name.lower() in z.lower()][:20]
        data = {"query": name, "found": False, "suggestions": matches}
        return {"status": "ok", "error": None, "data": data}
    now = datetime.now(tz)
    utc_offset = now.strftime("%z")
    data = {"timezone": name, "utc_offset": utc_offset, "current_time": now.isoformat(), "abbreviation": now.tzname()}
    return {"status": "ok", "error": None, "data": data}
