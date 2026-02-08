import re


ISBN10_RE = re.compile(r"^\d{9}[\dXx]$")
ISBN13_RE = re.compile(r"^\d{13}$")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _clean(isbn: str) -> str:
    return re.sub(r"[^0-9Xx]", "", isbn or "")


def _isbn10_check(isbn: str) -> bool:
    total = 0
    for i, ch in enumerate(isbn, start=1):
        if ch in "Xx":
            digit = 10
        else:
            digit = int(ch)
        total += i * digit
    return total % 11 == 0


def _isbn13_check(isbn: str) -> bool:
    total = 0
    for i, ch in enumerate(isbn):
        digit = int(ch)
        total += digit if i % 2 == 0 else digit * 3
    return total % 10 == 0


def run(params: dict) -> dict:
    params = params or {}
    isbn = params.get("isbn")
    if not isbn or not isinstance(isbn, str):
        return _error("Missing required parameter: isbn")

    cleaned = _clean(isbn)
    if ISBN10_RE.match(cleaned):
        valid = _isbn10_check(cleaned)
        isbn_type = "ISBN-10"
    elif ISBN13_RE.match(cleaned):
        valid = _isbn13_check(cleaned)
        isbn_type = "ISBN-13"
    else:
        return _error("Invalid ISBN format")

    data = {"isbn": isbn, "clean": cleaned, "type": isbn_type, "valid": valid}
    return {"status": "ok", "error": None, "data": data}
