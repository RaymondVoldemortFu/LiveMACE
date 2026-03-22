import random
import string

import requests


QUOTE_URL = "https://api.quotable.io/random"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _get_random_quote() -> str:
    response = requests.get(QUOTE_URL, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return payload.get("content") or ""


def _make_cipher() -> dict:
    letters = list(string.ascii_uppercase)
    shuffled = letters[:]
    random.shuffle(shuffled)
    return dict(zip(letters, shuffled))


def _encode(text: str, cipher: dict) -> str:
    encoded = []
    for ch in text:
        if ch.isalpha():
            encoded.append(cipher.get(ch.upper(), ch.upper()))
        else:
            encoded.append(ch)
    return "".join(encoded)


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    use_random = bool(params.get("random", False))

    if use_random or not text:
        try:
            text = _get_random_quote()
        except requests.RequestException as exc:
            return _error(f"Quote service error: {exc}")
        except ValueError:
            return _error("Quote service returned invalid JSON")

    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")
    if len(text) > 500:
        return _error("Text is too long (max 500 characters)")

    cipher = _make_cipher()
    encoded = _encode(text, cipher)
    words = [w for w in text.split() if w.strip()]
    letter_count = sum(1 for ch in text if ch.isalpha())

    data = {
        "encoded": encoded,
        "original": text,
        "cipher": cipher,
        "letterCount": letter_count,
        "wordCount": len(words),
        "html": f"<pre>{encoded}</pre>",
        "image": None,
        "solutionImage": None,
    }
    return {"status": "ok", "error": None, "data": data}
