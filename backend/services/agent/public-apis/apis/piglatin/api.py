import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _to_pig_latin(word: str) -> str:
    if not word or not re.match(r"^[A-Za-z]+$", word):
        return word
    w = word.lower()
    vowels = "aeiou"
    if w[0] in vowels:
        return word + "way"
    for i, c in enumerate(w):
        if c in vowels:
            return word[i:] + word[:i].lower() + "ay"
    return word + "ay"


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")
    text = str(text)
    words = re.findall(r"[A-Za-z]+|[^A-Za-z]+", text)
    result = "".join(_to_pig_latin(t) if t.isalpha() else t for t in words)
    data = {"text": text, "result": result}
    return {"status": "ok", "error": None, "data": data}
