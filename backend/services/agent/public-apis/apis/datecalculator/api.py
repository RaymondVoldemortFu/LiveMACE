from datetime import datetime


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _ordinal(num: int) -> str:
    if 10 <= num % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(num % 10, "th")
    return f"{num}{suffix}"


def _format_date(dt: datetime) -> dict:
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "day": dt.strftime("%A"),
        "month": dt.strftime("%B"),
        "year": dt.strftime("%Y"),
        "words": f"{dt.strftime('%A')}, {dt.strftime('%B')} {_ordinal(dt.day)} {dt.year}",
    }


def _month_diff(start: datetime, end: datetime) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _year_diff(start: datetime, end: datetime) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(0, years)


def run(params: dict) -> dict:
    params = params or {}
    start = params.get("start")
    end = params.get("end")

    if not start or not end:
        return _error("Missing required parameters: start, end")

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        return _error("Invalid date format: use YYYY-MM-DD")

    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    delta = end_dt - start_dt
    days = delta.days
    data = {
        "minutes": days * 24 * 60,
        "hours": days * 24,
        "days": days,
        "weeks": days // 7,
        "months": _month_diff(start_dt, end_dt),
        "years": _year_diff(start_dt, end_dt),
        "start": _format_date(start_dt),
        "end": _format_date(end_dt),
    }
    return {"status": "ok", "error": None, "data": data}
