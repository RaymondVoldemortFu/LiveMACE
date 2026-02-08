import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    password = params.get("password")
    if password is None:
        return _error("Missing required parameter: password")
    s = str(password)
    score = 0
    if len(s) >= 8:
        score += 1
    if len(s) >= 12:
        score += 1
    if len(s) >= 16:
        score += 1
    if re.search(r"[a-z]", s) and re.search(r"[A-Z]", s):
        score += 1
    if re.search(r"\d", s):
        score += 1
    if re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", s):
        score += 1
    if len(s) >= 20:
        score += 1
    strength = "weak" if score <= 2 else "medium" if score <= 4 else "strong"
    data = {"password": "*" * len(s), "score": score, "strength": strength, "length": len(s)}
    return {"status": "ok", "error": None, "data": data}
