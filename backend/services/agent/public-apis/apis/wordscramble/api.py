import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word") or params.get("text")
    if word is None or not str(word).strip():
        return _error("Missing required parameter: word")
    w = list(str(word).strip())
    random.shuffle(w)
    scrambled = "".join(w)
    data = {"word": str(word).strip(), "scrambled": scrambled}
    return {"status": "ok", "error": None, "data": data}
