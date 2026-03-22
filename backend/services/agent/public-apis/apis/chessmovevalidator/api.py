import re


CASTLING_RE = re.compile(r"^(O-O(-O)?)([+#])?$")
MOVE_RE = re.compile(
    r"^(?P<piece>[KQRBN])?"
    r"(?P<disambig>[a-h]?[1-8]?)"
    r"(?P<capture>x)?"
    r"(?P<to>[a-h][1-8])"
    r"(?P<promo>=?[QRBN])?"
    r"(?P<check>[+#])?$"
)


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    move = params.get("move")
    if not move or not isinstance(move, str):
        return _error("Missing required parameter: move")

    move = move.strip()
    if not move:
        return _error("Missing required parameter: move")

    castling_match = CASTLING_RE.match(move)
    if castling_match:
        is_checkmate = castling_match.group(3) == "#"
        is_check = castling_match.group(3) in {"+", "#"}
        return {
            "status": "ok",
            "error": None,
            "data": {
                "move": move,
                "valid": True,
                "type": "castling",
                "piece": "K",
                "capture": False,
                "check": is_check,
                "checkmate": is_checkmate,
                "promotion": False,
            },
        }

    match = MOVE_RE.match(move)
    if not match:
        return {
            "status": "ok",
            "error": None,
            "data": {
                "move": move,
                "valid": False,
                "type": "invalid",
                "piece": None,
                "capture": False,
                "check": False,
                "checkmate": False,
                "promotion": False,
            },
        }

    piece = match.group("piece") or "P"
    capture = bool(match.group("capture"))
    check_symbol = match.group("check")
    promotion = bool(match.group("promo"))
    move_type = "piece move" if piece != "P" else "pawn move"

    return {
        "status": "ok",
        "error": None,
        "data": {
            "move": move,
            "valid": True,
            "type": move_type,
            "piece": piece,
            "capture": capture,
            "check": check_symbol in {"+", "#"},
            "checkmate": check_symbol == "#",
            "promotion": promotion,
        },
    }
