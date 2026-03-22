def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    amount = params.get("amount") or params.get("bill")
    tip_percent = params.get("tip_percent") or params.get("tip") or 15
    people = params.get("people") or params.get("split") or 1
    if amount is None:
        return _error("Missing required parameter: amount")
    try:
        amount = float(amount)
        tip_percent = float(tip_percent)
        people = int(people)
    except (TypeError, ValueError):
        return _error("Invalid amount, tip_percent, or people")
    if amount < 0 or people < 1:
        return _error("Amount must be non-negative and people >= 1")
    tip_amount = round(amount * tip_percent / 100, 2)
    total = round(amount + tip_amount, 2)
    per_person = round(total / people, 2)
    data = {
        "amount": amount,
        "tip_percent": tip_percent,
        "tip_amount": tip_amount,
        "total": total,
        "people": people,
        "per_person": per_person,
    }
    return {"status": "ok", "error": None, "data": data}
