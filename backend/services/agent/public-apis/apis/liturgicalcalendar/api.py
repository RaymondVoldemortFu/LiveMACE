import datetime as _dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _easter_date(year: int) -> _dt.date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(year, month, day)


def run(params: dict) -> dict:
    params = params or {}
    year = params.get("year")
    if year is None:
        year = _dt.date.today().year
    try:
        year = int(year)
    except (TypeError, ValueError):
        return _error("Invalid year")

    easter = _easter_date(year)
    data = {
        "year": year,
        "easter": easter.isoformat(),
        "ash_wednesday": (easter - _dt.timedelta(days=46)).isoformat(),
        "palm_sunday": (easter - _dt.timedelta(days=7)).isoformat(),
        "pentecost": (easter + _dt.timedelta(days=49)).isoformat(),
        "advent_start": (_dt.date(year, 12, 25) - _dt.timedelta(days=28)).isoformat(),
    }
    return {"status": "ok", "error": None, "data": data}
