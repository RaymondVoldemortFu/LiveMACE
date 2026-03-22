import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _syllables(word: str) -> int:
    word = word.lower()
    if not word or not word.isalpha():
        return 0
    if len(word) <= 2:
        return 1
    word = re.sub(r"e$", "", word)
    vowels = re.findall(r"[aeiouy]+", word)
    return max(1, len(vowels))


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")
    s = str(text).strip()
    words = re.findall(r"[A-Za-z]+", s)
    total = sum(_syllables(w) for w in words)
    by_word = [{"word": w, "syllables": _syllables(w)} for w in words]
    data = {"text": s, "syllable_count": total, "word_count": len(words), "by_word": by_word}
    return {"status": "ok", "error": None, "data": data}
