import datetime as _dt
from zoneinfo import ZoneInfo


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    time_str = params.get("time")
    from_tz = params.get("from_tz")
    to_tz = params.get("to_tz")
    if not time_str or not from_tz or not to_tz:
        return _error("Missing required parameters: time, from_tz, to_tz")

    try:
        dt = _dt.datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=ZoneInfo(from_tz))
        converted = dt.astimezone(ZoneInfo(to_tz))
    except Exception:
        return _error("Invalid time or timezone")

    data = {"from": dt.isoformat(), "to": converted.isoformat()}
    return {"status": "ok", "error": None, "data": data}
