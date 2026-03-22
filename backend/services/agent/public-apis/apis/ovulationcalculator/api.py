import datetime as dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_date(v):
    try:
        return dt.datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def run(params: dict) -> dict:
    params = params or {}
    last_period = params.get("last_period")
    cycle_length = params.get("cycle_length", 28)
    try:
        cycle_length = int(cycle_length)
        if not 21 <= cycle_length <= 35:
            return _error("cycle_length should be between 21 and 35")
    except (TypeError, ValueError):
        return _error("Invalid cycle_length")
    start = _parse_date(last_period) if last_period else None
    if not start:
        return _error("Missing or invalid last_period (YYYY-MM-DD)")
    ovulation = start + dt.timedelta(days=cycle_length - 14)
    fertile_start = ovulation - dt.timedelta(days=5)
    fertile_end = ovulation + dt.timedelta(days=1)
    data = {
        "last_period": start.isoformat(),
        "cycle_length": cycle_length,
        "ovulation_date": ovulation.isoformat(),
        "fertile_window": {"start": fertile_start.isoformat(), "end": fertile_end.isoformat()},
    }
    return {"status": "ok", "error": None, "data": data}
