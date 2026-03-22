import random
from functools import lru_cache

import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


ANAGRAMS_URL = "https://raw.githubusercontent.com/words/english-anagrams/master/anagrams.json"


@lru_cache(maxsize=1)
def _load_anagrams() -> dict:
    response = requests.get(ANAGRAMS_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Invalid anagram dataset")
    return payload


def _scramble(word: str) -> str:
    letters = list(word)
    for _ in range(6):
        random.shuffle(letters)
        scrambled = "".join(letters)
        if scrambled.lower() != word.lower():
            return scrambled
    return "".join(letters)


def _difficulty_range(level: str) -> tuple:
    if level == "easy":
        return (3, 5)
    if level == "hard":
        return (8, 20)
    return (6, 7)


@lru_cache(maxsize=4)
def _candidate_words(level: str) -> list:
    anagrams = _load_anagrams()
    min_len, max_len = _difficulty_range(level)
    candidates = []
    for words in anagrams.values():
        for word in words:
            if word.isalpha() and min_len <= len(word) <= max_len:
                candidates.append(word)
    return candidates


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word")
    count = params.get("count", 1)
    difficulty = str(params.get("difficulty", "medium")).lower()

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer")
    if count < 1 or count > 20:
        return _error("Invalid count: must be between 1 and 20")

    try:
        anagrams = _load_anagrams()
    except requests.RequestException as exc:
        return _error(f"Anagram dataset error: {exc}")
    except ValueError:
        return _error("Anagram dataset returned invalid JSON")

    puzzles = []
    if word:
        if not isinstance(word, str):
            return _error("Invalid word: must be a string")
        key = "".join(sorted(word.lower()))
        options = anagrams.get(key) or []
        if not options:
            return _error("No anagrams found for the provided word")
        puzzles.append(
            {
                "word": word,
                "scrambled": _scramble(word),
                "anagrams": options,
                "hint": {"length": len(word), "uniqueLetters": len(set(word.lower()))},
            }
        )
    else:
        candidates = _candidate_words(difficulty if difficulty in {"easy", "medium", "hard"} else "medium")
        if not candidates:
            return _error("No candidate words available for this difficulty")
        for _ in range(count):
            word = random.choice(candidates)
            key = "".join(sorted(word.lower()))
            options = anagrams.get(key) or []
            puzzles.append(
                {
                    "word": word,
                    "scrambled": _scramble(word),
                    "anagrams": options,
                    "hint": {"length": len(word), "uniqueLetters": len(set(word.lower()))},
                }
            )

    data = {"count": len(puzzles), "difficulty": difficulty, "puzzles": puzzles}
    return {"status": "ok", "error": None, "data": data}
