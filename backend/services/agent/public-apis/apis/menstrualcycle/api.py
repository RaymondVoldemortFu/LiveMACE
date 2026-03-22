import datetime as _dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_date(value: str):
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def run(params: dict) -> dict:
    params = params or {}
    last_period = params.get("last_period")
    cycle_length = params.get("cycle_length", 28)
    period_length = params.get("period_length", 5)

    try:
        cycle_length = int(cycle_length)
        period_length = int(period_length)
    except (TypeError, ValueError):
        return _error("Invalid cycle_length/period_length")

    start = _parse_date(last_period) if last_period else _dt.date.today()
    if not start:
        return _error("Invalid last_period: YYYY-MM-DD")

    next_period = start + _dt.timedelta(days=cycle_length)
    ovulation = start + _dt.timedelta(days=cycle_length - 14)
    fertile_start = ovulation - _dt.timedelta(days=5)
    fertile_end = ovulation + _dt.timedelta(days=1)

    data = {
        "last_period": start.isoformat(),
        "next_period": next_period.isoformat(),
        "period_length": period_length,
        "cycle_length": cycle_length,
        "ovulation": ovulation.isoformat(),
        "fertile_window": {
            "start": fertile_start.isoformat(),
            "end": fertile_end.isoformat(),
        },
    }
    return {"status": "ok", "error": None, "data": data}
