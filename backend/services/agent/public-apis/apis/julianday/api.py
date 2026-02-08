import datetime as _dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _to_jdn(date: _dt.date) -> int:
    a = (14 - date.month) // 12
    y = date.year + 4800 - a
    m = date.month + 12 * a - 3
    return date.day + ((153 * m + 2) // 5) + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def _from_jdn(jdn: int) -> _dt.date:
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + (m // 10)
    return _dt.date(year, month, day)


def run(params: dict) -> dict:
    params = params or {}
    date_str = params.get("date")
    jdn = params.get("jdn")

    if date_str:
        try:
            date = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return _error("Invalid date format: YYYY-MM-DD")
        jdn_value = _to_jdn(date)
        data = {"date": date.isoformat(), "jdn": jdn_value}
        return {"status": "ok", "error": None, "data": data}

    if jdn is not None:
        try:
            jdn_value = int(jdn)
        except (TypeError, ValueError):
            return _error("Invalid jdn")
        date = _from_jdn(jdn_value)
        data = {"date": date.isoformat(), "jdn": jdn_value}
        return {"status": "ok", "error": None, "data": data}

    return _error("Missing required parameter: date or jdn")
