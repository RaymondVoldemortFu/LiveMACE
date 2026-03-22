from datetime import date, datetime, time
from typing import Tuple


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
]
TEENS = [
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = [
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]

ORDINAL_UNITS = {
    "one": "first",
    "two": "second",
    "three": "third",
    "four": "fourth",
    "five": "fifth",
    "six": "sixth",
    "seven": "seventh",
    "eight": "eighth",
    "nine": "ninth",
    "ten": "tenth",
    "eleven": "eleventh",
    "twelve": "twelfth",
    "thirteen": "thirteenth",
    "fourteen": "fourteenth",
    "fifteen": "fifteenth",
    "sixteen": "sixteenth",
    "seventeen": "seventeenth",
    "eighteen": "eighteenth",
    "nineteen": "nineteenth",
}
ORDINAL_TENS = {
    "twenty": "twentieth",
    "thirty": "thirtieth",
    "forty": "fortieth",
    "fifty": "fiftieth",
    "sixty": "sixtieth",
    "seventy": "seventieth",
    "eighty": "eightieth",
    "ninety": "ninetieth",
}


def _number_to_words(n: int) -> str:
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        if ones == 0:
            return TENS[tens]
        return f"{TENS[tens]}-{ONES[ones]}"
    if n < 1000:
        hundreds, remainder = divmod(n, 100)
        if remainder == 0:
            return f"{ONES[hundreds]} hundred"
        return f"{ONES[hundreds]} hundred {_number_to_words(remainder)}"
    if n < 10000:
        thousands, remainder = divmod(n, 1000)
        if remainder == 0:
            return f"{ONES[thousands]} thousand"
        return f"{ONES[thousands]} thousand {_number_to_words(remainder)}"
    return str(n)


def _words_to_ordinal(words: str) -> str:
    if "-" in words:
        head, tail = words.rsplit("-", 1)
        return f"{head}-{ORDINAL_UNITS.get(tail, tail + 'th')}"
    tokens = words.split()
    if not tokens:
        return words
    last = tokens[-1]
    if last in ORDINAL_UNITS:
        tokens[-1] = ORDINAL_UNITS[last]
    elif last in ORDINAL_TENS:
        tokens[-1] = ORDINAL_TENS[last]
    elif last == "hundred":
        tokens[-1] = "hundredth"
    elif last == "thousand":
        tokens[-1] = "thousandth"
    else:
        tokens[-1] = last + "th"
    return " ".join(tokens)


def _age_years(dob: date, today: date) -> int:
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return max(0, years)


def _age_months(dob: date, today: date) -> int:
    months = (today.year - dob.year) * 12 + (today.month - dob.month)
    if today.day < dob.day:
        months -= 1
    return max(0, months)


def _next_birthday(dob: date, now: datetime) -> Tuple[datetime, int]:
    candidate = date(now.year, dob.month, dob.day)
    candidate_dt = datetime.combine(candidate, now.time(), tzinfo=now.tzinfo)
    if candidate_dt < now:
        candidate = date(now.year + 1, dob.month, dob.day)
        candidate_dt = datetime.combine(candidate, now.time(), tzinfo=now.tzinfo)
    month_diff = (candidate.year - now.year) * 12 + (candidate.month - now.month)
    if candidate.day < now.day:
        month_diff -= 1
    return candidate_dt, max(0, month_diff)


def run(params: dict) -> dict:
    dob_str = (params or {}).get("dob")
    if not dob_str or not isinstance(dob_str, str):
        return _error("Missing required parameter: dob")
    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except ValueError:
        return _error("Invalid dob format, expected YYYY-MM-DD")

    now = datetime.now().astimezone()
    today = now.date()
    if dob > today:
        return _error("dob cannot be in the future")

    delta = now - datetime.combine(dob, time.min, tzinfo=now.tzinfo)
    total_seconds = int(delta.total_seconds())
    days = delta.days
    weeks = days // 7
    hours = total_seconds // 3600
    minutes = total_seconds // 60

    years = _age_years(dob, today)
    months = _age_months(dob, today)

    years_words = _number_to_words(years)
    ordinal_words = _words_to_ordinal(years_words)

    next_bday_dt, month_diff = _next_birthday(dob, now)
    next_delta = next_bday_dt - now
    next_seconds = max(0, int(next_delta.total_seconds()))
    next_days = next_delta.days
    next_weeks = next_days // 7
    next_hours = next_seconds // 3600
    next_minutes = next_seconds // 60

    tzinfo = now.tzinfo
    timezone_name = getattr(tzinfo, "key", None) or (tzinfo.tzname(now) if tzinfo else "UTC")

    return {
        "status": "ok",
        "error": None,
        "data": {
            "dob": dob_str,
            "age_breakdown": {
                "years": years,
                "months": months,
                "weeks": weeks,
                "days": days,
                "hours": hours,
                "minutes": minutes,
                "seconds": total_seconds,
            },
            "age_words": {
                "years": years_words,
                "ordinal": ordinal_words,
                "full": f"{years_words} years old",
                "locale": "en-US",
            },
            "timezone": timezone_name,
            "locale": "en-US",
            "next_birthday": {
                "months": max(0, month_diff),
                "weeks": max(0, next_weeks),
                "days": max(0, next_days),
                "hours": max(0, next_hours),
                "minutes": max(0, next_minutes),
                "seconds": max(0, next_seconds),
            },
        },
    }
