ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]


def _to_words(n: int) -> str:
    if n == 0:
        return "zero"
    if n < 0:
        return "minus " + _to_words(-n)
    if n < 10:
        return ONES[n]
    if n < 20:
        return TEENS[n - 10]
    if n < 100:
        return (TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")).strip()
    if n < 1000:
        return (ONES[n // 100] + " hundred" + (" " + _to_words(n % 100) if n % 100 else "")).strip()
    if n < 1_000_000:
        return (_to_words(n // 1000) + " thousand" + (" " + _to_words(n % 1000) if n % 1000 else "")).strip()
    if n < 1_000_000_000:
        return (_to_words(n // 1_000_000) + " million" + (" " + _to_words(n % 1_000_000) if n % 1_000_000 else "")).strip()
    return (_to_words(n // 1_000_000_000) + " billion" + (" " + _to_words(n % 1_000_000_000) if n % 1_000_000_000 else "")).strip()


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    number = params.get("number")
    if number is None:
        return _error("Missing required parameter: number")
    try:
        n = int(float(str(number).strip()))
    except (TypeError, ValueError):
        return _error("Invalid number")
    if abs(n) > 999_999_999_999:
        return _error("Number out of range")
    data = {"number": n, "words": _to_words(n)}
    return {"status": "ok", "error": None, "data": data}
