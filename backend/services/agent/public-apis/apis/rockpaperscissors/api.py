import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    choices = ["rock", "paper", "scissors"]
    user_choice = (params.get("choice") or params.get("player")).strip().lower() if params.get("choice") or params.get("player") else None
    if user_choice and user_choice not in choices:
        return _error("choice must be rock, paper, or scissors")
    player = user_choice or random.choice(choices)
    computer = random.choice(choices)
    beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    if player == computer:
        result = "tie"
    elif beats[player] == computer:
        result = "player"
    else:
        result = "computer"
    data = {"player": player, "computer": computer, "result": result}
    return {"status": "ok", "error": None, "data": data}
