import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


SYMBOLS = ["cherry", "lemon", "orange", "plum", "bell", "bar", "seven"]


def run(params: dict) -> dict:
    params = params or {}
    reels = params.get("reels") or 3
    bet = params.get("bet") or 1
    try:
        reels = int(reels)
        reels = max(2, min(5, reels))
    except (TypeError, ValueError):
        reels = 3
    try:
        bet = float(bet)
        bet = max(0.01, bet)
    except (TypeError, ValueError):
        bet = 1
    spin = [random.choice(SYMBOLS) for _ in range(reels)]
    payouts = {"seven": 10, "bar": 5, "bell": 4, "plum": 3, "orange": 2, "lemon": 1, "cherry": 1}
    if len(set(spin)) == 1:
        multiplier = payouts.get(spin[0], 1)
    elif len(set(spin)) == 2 and spin.count(spin[0]) == 2:
        multiplier = 0.5
    else:
        multiplier = 0
    win = round(bet * multiplier, 2)
    data = {"reels": reels, "spin": spin, "bet": bet, "win": win, "multiplier": multiplier}
    return {"status": "ok", "error": None, "data": data}
