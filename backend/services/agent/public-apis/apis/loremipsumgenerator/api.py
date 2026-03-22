import random


WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua"
).split()


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count", 50)
    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count")
    if count < 1:
        return _error("Invalid count")

    text = " ".join(random.choice(WORDS) for _ in range(count))
    data = {"text": text, "count": count}
    return {"status": "ok", "error": None, "data": data}
