import random
from functools import lru_cache

import requests


WORDS_URL = "https://raw.githubusercontent.com/first20hours/google-10000-english/master/20k.txt"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


@lru_cache(maxsize=1)
def _load_words() -> list:
    response = requests.get(WORDS_URL, timeout=20)
    response.raise_for_status()
    words = [line.strip() for line in response.text.splitlines() if line.strip().isalpha()]
    return words


def run(params: dict) -> dict:
    try:
        words = _load_words()
    except requests.RequestException as exc:
        return _error(f"Hangman word list error: {exc}")

    word = random.choice(words) if words else None
    data = {"word": word, "length": len(word) if word else None}
    return {"status": "ok", "error": None, "data": data}
