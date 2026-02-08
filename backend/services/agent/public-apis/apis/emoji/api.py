from functools import lru_cache

import requests


EMOJI_URL = "https://raw.githubusercontent.com/github/gemoji/master/db/emoji.json"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


@lru_cache(maxsize=1)
def _load_emojis() -> list:
    response = requests.get(EMOJI_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Invalid emoji dataset")
    return payload


def run(params: dict) -> dict:
    params = params or {}
    query = params.get("query") or params.get("name") or params.get("emoji")

    try:
        emojis = _load_emojis()
    except requests.RequestException as exc:
        return _error(f"Emoji dataset error: {exc}")
    except ValueError:
        return _error("Emoji dataset returned invalid JSON")

    results = emojis
    if query and isinstance(query, str):
        needle = query.strip().lower()
        results = [
            item
            for item in emojis
            if needle in str(item.get("description", "")).lower()
            or needle in " ".join(item.get("aliases") or []).lower()
            or needle == str(item.get("emoji", "")).lower()
        ]

    data = {
        "count": len(results),
        "emojis": [
            {
                "emoji": item.get("emoji"),
                "name": item.get("description"),
                "category": item.get("category"),
                "aliases": item.get("aliases"),
                "tags": item.get("tags"),
            }
            for item in results
        ],
    }
    return {"status": "ok", "error": None, "data": data}
