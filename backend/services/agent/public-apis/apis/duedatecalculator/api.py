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
    lmp = params.get("lmp")
    conception = params.get("conception")
    cycle_length = params.get("cycle_length", 28)

    try:
        cycle_length = int(cycle_length)
    except (TypeError, ValueError):
        return _error("Invalid cycle_length: must be an integer")

    lmp_date = _parse_date(lmp) if lmp else None
    conception_date = _parse_date(conception) if conception else None

    if not lmp_date and not conception_date:
        return _error("Missing required parameter: lmp or conception (YYYY-MM-DD)")

    if lmp_date:
        due_date = lmp_date + _dt.timedelta(days=280 + (cycle_length - 28))
        base_date = lmp_date
        method = "lmp"
    else:
        due_date = conception_date + _dt.timedelta(days=266)
        base_date = conception_date - _dt.timedelta(days=14)
        method = "conception"

    today = _dt.date.today()
    gestational_age_days = (today - base_date).days if today >= base_date else None
    gestational_age_weeks = gestational_age_days // 7 if gestational_age_days is not None else None

    trimester_1_end = base_date + _dt.timedelta(days=13 * 7)
    trimester_2_end = base_date + _dt.timedelta(days=27 * 7)

    data = {
        "method": method,
        "lmp": lmp_date.isoformat() if lmp_date else None,
        "conception": conception_date.isoformat() if conception_date else None,
        "due_date": due_date.isoformat(),
        "gestational_age_days": gestational_age_days,
        "gestational_age_weeks": gestational_age_weeks,
        "trimester_dates": {
            "trimester_1_end": trimester_1_end.isoformat(),
            "trimester_2_end": trimester_2_end.isoformat(),
            "trimester_3_end": due_date.isoformat(),
        },
    }
    return {"status": "ok", "error": None, "data": data}
