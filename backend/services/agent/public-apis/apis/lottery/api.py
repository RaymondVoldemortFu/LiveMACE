import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    numbers = params.get("numbers", 6)
    max_number = params.get("max", 49)

    try:
        numbers = int(numbers)
        max_number = int(max_number)
    except (TypeError, ValueError):
        return _error("Invalid numbers/max")

    if numbers < 1 or max_number <= numbers:
        return _error("Invalid numbers/max")

    picks = random.sample(range(1, max_number + 1), numbers)
    picks.sort()
    data = {"numbers": picks}
    return {"status": "ok", "error": None, "data": data}
