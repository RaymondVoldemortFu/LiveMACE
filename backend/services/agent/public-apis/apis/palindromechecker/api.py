import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize(s: str, ignore_case: bool, ignore_spaces: bool, ignore_punctuation: bool) -> str:
    if ignore_punctuation:
        s = re.sub(r"[^\w\s]", "", s)
    if ignore_spaces:
        s = s.replace(" ", "")
    if ignore_case:
        s = s.lower()
    return s


def _longest_palindrome(s: str) -> str:
    n = len(s)
    if n <= 1:
        return s
    best = ""
    for i in range(n):
        for j in range(i + 1, n + 1):
            sub = s[i:j]
            if sub == sub[::-1] and len(sub) > len(best):
                best = sub
    return best or (s[0] if s else "")


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")
    text = str(text)
    ignore_case = params.get("ignore_case", True)
    ignore_spaces = params.get("ignore_spaces", True)
    ignore_punctuation = params.get("ignore_punctuation", True)
    if isinstance(ignore_case, str):
        ignore_case = ignore_case.lower() in ("1", "true", "yes")
    if isinstance(ignore_spaces, str):
        ignore_spaces = ignore_spaces.lower() in ("1", "true", "yes")
    if isinstance(ignore_punctuation, str):
        ignore_punctuation = ignore_punctuation.lower() in ("1", "true", "yes")
    normalized = _normalize(text, ignore_case, ignore_spaces, ignore_punctuation)
    is_palindrome = normalized == normalized[::-1] if normalized else True
    longest = _longest_palindrome(normalized) if normalized else ""
    data = {
        "text": text,
        "is_palindrome": is_palindrome,
        "longest_palindromic_substring": longest,
        "normalized": normalized,
    }
    return {"status": "ok", "error": None, "data": data}
