import random


RESPONSES = [
    "It is certain.",
    "Ask again later.",
    "My reply is no.",
    "Signs point to yes.",
    "Cannot predict now.",
    "Very doubtful.",
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    answer = random.choice(RESPONSES)
    return {"status": "ok", "error": None, "data": {"answer": answer}}
