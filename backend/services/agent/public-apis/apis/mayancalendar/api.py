import datetime as _dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


TZOLKIN_NAMES = [
    "Imix", "Ik'", "Ak'b'al", "K'an", "Chikchan", "Kimi", "Manik'", "Lamat", "Muluk", "Ok",
    "Chuwen", "Eb", "B'en", "Ix", "Men", "K'ib", "Kab'an", "Etz'nab'", "Kawak", "Ajaw",
]
HAAB_MONTHS = [
    "Pop", "Wo", "Sip", "Sotz'", "Sek", "Xul", "Yaxk'in", "Mol", "Ch'en", "Yax",
    "Sak'", "Keh", "Mak", "K'ank'in", "Muwan", "Pax", "K'ayab", "Kumk'u", "Wayeb",
]


def _to_jdn(date: _dt.date) -> int:
    a = (14 - date.month) // 12
    y = date.year + 4800 - a
    m = date.month + 12 * a - 3
    return date.day + ((153 * m + 2) // 5) + 365 * y + y // 4 - y // 100 + y // 400 - 32045


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

    jdn = _to_jdn(date)
    # GMT correlation constant
    mayan_day = jdn - 584283

    baktun = mayan_day // 144000
    katun = (mayan_day % 144000) // 7200
    tun = (mayan_day % 7200) // 360
    uinal = (mayan_day % 360) // 20
    kin = mayan_day % 20

    tzolkin_num = (mayan_day % 13) + 1
    tzolkin_name = TZOLKIN_NAMES[mayan_day % 20]
    haab_day = (mayan_day + 348) % 365
    haab_month = HAAB_MONTHS[haab_day // 20]
    haab_num = haab_day % 20

    data = {
        "date": date.isoformat(),
        "long_count": f"{baktun}.{katun}.{tun}.{uinal}.{kin}",
        "tzolkin": {"number": tzolkin_num, "name": tzolkin_name},
        "haab": {"month": haab_month, "day": haab_num},
    }
    return {"status": "ok", "error": None, "data": data}
