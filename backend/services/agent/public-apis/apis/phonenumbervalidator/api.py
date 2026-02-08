import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    number = params.get("number") or params.get("phone") or params.get("phonenumber")
    country = (params.get("country") or params.get("country_code") or "").strip().upper() or "US"
    if number is None or str(number).strip() == "":
        return _error("Missing required parameter: number")
    s = re.sub(r"[\s\-\.\(\)]", "", str(number).strip())
    if not s.replace("+", "").isdigit():
        return _error("Invalid character in phone number")
    if s.startswith("+"):
        has_plus = True
        digits = s[1:]
    else:
        has_plus = False
        digits = s.lstrip("0")
    if len(digits) < 10:
        data = {"valid": False, "number": number, "normalized": s, "reason": "Too few digits"}
        return {"status": "ok", "error": None, "data": data}
    if len(digits) > 15:
        data = {"valid": False, "number": number, "normalized": s, "reason": "Too many digits"}
        return {"status": "ok", "error": None, "data": data}
    normalized = "+" + digits if has_plus or len(digits) > 10 else digits
    data = {"valid": True, "number": number, "normalized": normalized, "country": country}
    return {"status": "ok", "error": None, "data": data}
