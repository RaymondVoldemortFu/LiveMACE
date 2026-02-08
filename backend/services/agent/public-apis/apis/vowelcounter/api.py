import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text)
    vowels = "aeiouAEIOU"
    v_count = sum(1 for c in s if c in vowels)
    cons = sum(1 for c in s if c.isalpha() and c not in vowels)
    data = {"text": s, "vowel_count": v_count, "consonant_count": cons, "total_letters": v_count + cons}
    return {"status": "ok", "error": None, "data": data}
