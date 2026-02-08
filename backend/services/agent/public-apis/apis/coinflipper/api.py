import random
from typing import List, Tuple


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _longest_streak(flips: List[str]) -> Tuple[int, str]:
    if not flips:
        return 0, ""
    best_len = 1
    best_type = flips[0]
    current_len = 1
    current_type = flips[0]
    for flip in flips[1:]:
        if flip == current_type:
            current_len += 1
        else:
            if current_len > best_len:
                best_len = current_len
                best_type = current_type
            current_type = flip
            current_len = 1
    if current_len > best_len:
        best_len = current_len
        best_type = current_type
    return best_len, best_type


def run(params: dict) -> dict:
    params = params or {}
    flips = params.get("flips", 1)

    try:
        flips = int(flips)
    except (TypeError, ValueError):
        return _error("Invalid flips: must be an integer between 1 and 10000")

    if flips < 1 or flips > 10000:
        return _error("Invalid flips: must be between 1 and 10000")

    results = [random.choice(["Heads", "Tails"]) for _ in range(flips)]
    heads_count = results.count("Heads")
    tails_count = results.count("Tails")
    heads_percentage = round(heads_count / flips * 100, 2)
    tails_percentage = round(tails_count / flips * 100, 2)
    streak_len, streak_type = _longest_streak(results)
    is_fair = abs(heads_count - tails_count) / flips <= 0.1

    return {
        "status": "ok",
        "error": None,
        "data": {
            "total_flips": flips,
            "flips": results,
            "heads_count": heads_count,
            "tails_count": tails_count,
            "heads_percentage": heads_percentage,
            "tails_percentage": tails_percentage,
            "longest_streak": {"length": streak_len, "type": streak_type or None},
            "first_flip": results[0],
            "last_flip": results[-1],
            "is_fair": is_fair,
        },
    }
