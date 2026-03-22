import random
import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_dice(expr: str) -> tuple:
    match = re.fullmatch(r"\s*(\d+)?d(\d+)\s*", expr.lower())
    if not match:
        raise ValueError("Invalid dice notation")
    num = int(match.group(1) or 1)
    sides = int(match.group(2))
    return num, sides


def run(params: dict) -> dict:
    params = params or {}
    dice = params.get("dice", "1d6")
    modifier = params.get("modifier", 0)

    try:
        num_dice, num_sides = _parse_dice(str(dice))
    except ValueError:
        return _error("Invalid dice notation: use XdY format")

    if num_dice < 1 or num_dice > 100:
        return _error("Invalid dice count: must be between 1 and 100")
    if num_sides < 2 or num_sides > 1000:
        return _error("Invalid dice sides: must be between 2 and 1000")

    try:
        modifier = int(modifier)
    except (TypeError, ValueError):
        return _error("Invalid modifier: must be an integer")

    rolls = [random.randint(1, num_sides) for _ in range(num_dice)]
    total = sum(rolls)
    total_with_modifier = total + modifier
    expression = f"{num_dice}d{num_sides}{'+' if modifier >= 0 else ''}{modifier}"
    data = {
        "dice_notation": f"{num_dice}d{num_sides}",
        "num_dice": num_dice,
        "num_sides": num_sides,
        "modifier": modifier,
        "rolls": rolls,
        "total": total,
        "total_with_modifier": total_with_modifier,
        "min_roll": min(rolls),
        "max_roll": max(rolls),
        "average_roll": round(total / num_dice, 2),
        "theoretical_min": num_dice,
        "theoretical_max": num_dice * num_sides,
        "theoretical_average": round(num_dice * (num_sides + 1) / 2, 2),
        "expression": expression,
    }
    return {"status": "ok", "error": None, "data": data}
