import re


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3}[\s.-]?\d{4}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
URL_RE = re.compile(r"\bhttps?://[^\s]+", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})\b"
)


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _luhn_check(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    parity = len(digits) % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _mask(pattern: re.Pattern, text: str, label: str, counts: dict) -> str:
    def replacer(match: re.Match) -> str:
        counts[label] += 1
        return f"[{label.upper()}]"

    return pattern.sub(replacer, text)


def _mask_credit_cards(text: str, counts: dict) -> str:
    pattern = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

    def replacer(match: re.Match) -> str:
        if _luhn_check(match.group(0)):
            counts["credit_card"] += 1
            return "[CREDIT_CARD]"
        return match.group(0)

    return pattern.sub(replacer, text)


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    types = params.get("types")

    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    if types is None:
        types = ["email", "phone", "ssn", "credit_card", "ip_address", "url", "date"]
    elif isinstance(types, str):
        types = [t.strip().lower() for t in types.split(",") if t.strip()]
    elif isinstance(types, list):
        types = [str(t).lower() for t in types]
    else:
        return _error("Invalid types: must be a list or comma-separated string")

    counts = {
        "email": 0,
        "phone": 0,
        "ssn": 0,
        "credit_card": 0,
        "ip_address": 0,
        "url": 0,
        "date": 0,
    }

    masked = text
    if "email" in types:
        masked = _mask(EMAIL_RE, masked, "email", counts)
    if "phone" in types:
        masked = _mask(PHONE_RE, masked, "phone", counts)
    if "ssn" in types:
        masked = _mask(SSN_RE, masked, "ssn", counts)
    if "credit_card" in types:
        masked = _mask_credit_cards(masked, counts)
    if "ip_address" in types:
        masked = _mask(IP_RE, masked, "ip_address", counts)
    if "url" in types:
        masked = _mask(URL_RE, masked, "url", counts)
    if "date" in types:
        masked = _mask(DATE_RE, masked, "date", counts)

    return {"status": "ok", "error": None, "data": {"masked": masked, "detected": counts}}
