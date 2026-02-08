# Standard English Scrabble letter values (game rules, not a replacement list)
SCORES = {
    "A": 1, "B": 3, "C": 3, "D": 2, "E": 1, "F": 4, "G": 2, "H": 4, "I": 1, "J": 8, "K": 5, "L": 1,
    "M": 3, "N": 1, "O": 1, "P": 3, "Q": 10, "R": 1, "S": 1, "T": 1, "U": 1, "V": 4, "W": 4, "X": 8, "Y": 4, "Z": 10,
}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word") or params.get("text")
    if word is None or not str(word).strip():
        return _error("Missing required parameter: word")
    w = str(word).strip().upper().replace(" ", "")
    if not w.isalpha():
        return _error("Word must contain only letters")
    total = sum(SCORES.get(c, 0) for c in w)
    breakdown = [{"letter": c, "score": SCORES.get(c, 0)} for c in w]
    data = {"word": word, "score": total, "breakdown": breakdown}
    return {"status": "ok", "error": None, "data": data}
