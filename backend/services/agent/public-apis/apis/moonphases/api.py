import datetime as _dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _phase_name(phase: float) -> str:
    if phase < 0.03 or phase > 0.97:
        return "New Moon"
    if phase < 0.22:
        return "Waxing Crescent"
    if phase < 0.28:
        return "First Quarter"
    if phase < 0.47:
        return "Waxing Gibbous"
    if phase < 0.53:
        return "Full Moon"
    if phase < 0.72:
        return "Waning Gibbous"
    if phase < 0.78:
        return "Last Quarter"
    return "Waning Crescent"


def run(params: dict) -> dict:
    params = params or {}
    date_str = params.get("date")
    if date_str:
        try:
            date = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return _error("Invalid date format: YYYY-MM-DD")
    else:
        date = _dt.date.today()

    # Simple approximation based on known new moon reference
    known_new_moon = _dt.date(2000, 1, 6)
    days = (date - known_new_moon).days
    synodic_month = 29.53058867
    phase = (days % synodic_month) / synodic_month
    name = _phase_name(phase)

    data = {"date": date.isoformat(), "phase": round(phase, 3), "name": name}
    return {"status": "ok", "error": None, "data": data}
