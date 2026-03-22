import hashlib


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    email = params.get("email")
    size = params.get("size", 200)

    if not email or not isinstance(email, str):
        return _error("Missing required parameter: email")
    try:
        size = int(size)
    except (TypeError, ValueError):
        return _error("Invalid size: must be an integer")

    normalized = email.strip().lower()
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
    url = f"https://www.gravatar.com/avatar/{digest}?s={size}&d=identicon"

    data = {"email": email, "hash": digest, "url": url}
    return {"status": "ok", "error": None, "data": data}
