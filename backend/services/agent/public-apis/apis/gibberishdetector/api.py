import re


VOWELS = set("aeiou")
WORD_RE = re.compile(r"[a-zA-Z]+")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _score(text: str) -> float:
    letters = [c.lower() for c in text if c.isalpha()]
    if not letters:
        return 1.0
    vowel_ratio = sum(1 for c in letters if c in VOWELS) / len(letters)
    bigrams = [letters[i] + letters[i + 1] for i in range(len(letters) - 1)]
    bad_bigrams = sum(1 for bg in bigrams if bg[0] not in VOWELS and bg[1] not in VOWELS)
    bad_ratio = bad_bigrams / max(1, len(bigrams))
    return (0.6 * abs(0.4 - vowel_ratio)) + (0.4 * bad_ratio)


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    score = _score(text)
    gibberish = score > 0.35
    data = {"text": text, "gibberish": gibberish, "score": round(score, 3)}
    return {"status": "ok", "error": None, "data": data}
