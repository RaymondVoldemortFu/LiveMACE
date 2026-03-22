import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _lemma(word: str) -> str:
    if word.endswith("ing") and len(word) > 4:
        return word[:-3]
    if word.endswith("ed") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    words = re.findall(r"[A-Za-z']+", text)
    lemmas = [_lemma(w.lower()) for w in words]
    data = {"tokens": words, "lemmas": lemmas}
    return {"status": "ok", "error": None, "data": data}
