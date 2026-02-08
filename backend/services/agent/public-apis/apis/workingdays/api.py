from datetime import datetime, timedelta


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse(s):
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def run(params: dict) -> dict:
    params = params or {}
    start = params.get("start") or params.get("start_date")
    end = params.get("end") or params.get("end_date")
    if start is None or end is None:
        return _error("Missing required parameters: start, end (YYYY-MM-DD)")
    d1 = _parse(start)
    d2 = _parse(end)
    if not d1 or not d2:
        return _error("Invalid date format; use YYYY-MM-DD")
    if d1 > d2:
        d1, d2 = d2, d1
    count = 0
    cur = d1
    while cur <= d2:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    data = {"start": d1.isoformat(), "end": d2.isoformat(), "working_days": count}
    return {"status": "ok", "error": None, "data": data}
