import requests


LEXICON_URL = "https://raw.githubusercontent.com/dwyl/english-words/master/words_dictionary.json"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word")
    if not word or not isinstance(word, str):
        return _error("Missing required parameter: word")

    try:
        response = requests.get(LEXICON_URL, timeout=20)
        response.raise_for_status()
        words = response.json()
    except requests.RequestException as exc:
        return _error(f"Lexicon data error: {exc}")
    except ValueError:
        return _error("Lexicon data returned invalid JSON")

    exists = word.lower() in words
    data = {"word": word, "exists": exists}
    return {"status": "ok", "error": None, "data": data}
