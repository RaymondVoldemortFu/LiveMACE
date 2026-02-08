import random


SAFE_COMPLIMENTS = [
    "Your positive energy is contagious.",
    "You have an amazing sense of style.",
    "Your creativity is inspiring.",
    "You make complex things look easy.",
    "Your kindness stands out.",
    "You bring out the best in people.",
    "Your work ethic is impressive.",
    "You have a great sense of humor.",
    "You always make time for others.",
    "You light up the room.",
]

ADULT_COMPLIMENTS = [
    "You have a magnetic charm that's hard to ignore.",
    "Your smile is absolutely captivating.",
    "You make confidence look effortless.",
    "You have a wonderfully romantic vibe.",
    "You're as charming as you are thoughtful.",
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    adult = bool(params.get("adult", False))

    pool = SAFE_COMPLIMENTS[:]
    if adult:
        pool.extend(ADULT_COMPLIMENTS)

    compliment = random.choice(pool)
    return {"status": "ok", "error": None, "data": {"compliment": compliment}}
