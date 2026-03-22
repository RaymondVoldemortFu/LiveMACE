import random
from functools import lru_cache

import requests


FORTUNE_URL = "https://raw.githubusercontent.com/bmc/fortunes/master/fortunes"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


@lru_cache(maxsize=1)
def _load_fortunes() -> list:
    response = requests.get(FORTUNE_URL, timeout=20)
    response.raise_for_status()
    text = response.text
    fortunes = [chunk.strip() for chunk in text.split("%") if chunk.strip()]
    return fortunes


def run(params: dict) -> dict:
    try:
        fortunes = _load_fortunes()
    except requests.RequestException as exc:
        return _error(f"Fortune data error: {exc}")

    fortune = random.choice(fortunes) if fortunes else None
    data = {"fortune": fortune}
    return {"status": "ok", "error": None, "data": data}
