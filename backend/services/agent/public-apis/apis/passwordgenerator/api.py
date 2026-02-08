import random
import string


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    length = params.get("length", 16)
    use_uppercase = params.get("uppercase", True)
    use_lowercase = params.get("lowercase", True)
    use_digits = params.get("digits", True)
    use_special = params.get("special", True)
    try:
        length = int(length)
        length = max(4, min(128, length))
    except (TypeError, ValueError):
        length = 16
    if isinstance(use_uppercase, str):
        use_uppercase = use_uppercase.lower() in ("1", "true", "yes")
    if isinstance(use_lowercase, str):
        use_lowercase = use_lowercase.lower() in ("1", "true", "yes")
    if isinstance(use_digits, str):
        use_digits = use_digits.lower() in ("1", "true", "yes")
    if isinstance(use_special, str):
        use_special = use_special.lower() in ("1", "true", "yes")
    pool = ""
    if use_lowercase:
        pool += string.ascii_lowercase
    if use_uppercase:
        pool += string.ascii_uppercase
    if use_digits:
        pool += string.digits
    if use_special:
        pool += "!@#$%^&*()_+-=[]{}|;:,.<>?"
    if not pool:
        pool = string.ascii_letters + string.digits
    password = "".join(random.choices(pool, k=length))
    data = {"password": password, "length": len(password)}
    return {"status": "ok", "error": None, "data": data}
