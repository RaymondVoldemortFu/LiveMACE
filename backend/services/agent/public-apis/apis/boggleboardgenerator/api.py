import random
from typing import List


BOGGLE_4x4_DICE = [
    "AAEEGN",
    "ABBJOO",
    "ACHOPS",
    "AFFKPS",
    "AOOTTW",
    "CIMOTU",
    "DEILRX",
    "DELRVY",
    "DISTTY",
    "EEGHNW",
    "EEINSU",
    "EHRTVW",
    "EIOSST",
    "ELRTTY",
    "HIMNQU",
    "HLNNRZ",
]

BOGGLE_5x5_DICE = [
    "AAAFRS",
    "AAEEEE",
    "AAFIRS",
    "ADENNN",
    "AEEEEM",
    "AEEGMU",
    "AEGMNN",
    "AFIRSY",
    "BJKQXZ",
    "CCNSTW",
    "CEIILT",
    "CEILPT",
    "CEIPST",
    "DDHNOT",
    "DHHLOR",
    "DHHNOT",
    "DHLNOR",
    "EIIITT",
    "EMOTTT",
    "ENSSSU",
    "FIPRSY",
    "GORRVW",
    "IPRRRY",
    "NOOTUW",
    "OOOTTU",
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _generate_board(size: int) -> List[List[str]]:
    dice = BOGGLE_4x4_DICE if size == 4 else BOGGLE_5x5_DICE
    dice = dice[: size * size]
    random.shuffle(dice)
    letters = [random.choice(die) for die in dice]
    board = [letters[i : i + size] for i in range(0, len(letters), size)]
    return board


def _build_html(board: List[List[str]]) -> str:
    rows = []
    for row in board:
        cells = "".join(f"<td>{letter}</td>" for letter in row)
        rows.append(f"<tr>{cells}</tr>")
    return "<table>" + "".join(rows) + "</table>"


def run(params: dict) -> dict:
    params = params or {}
    size = params.get("size", 4)

    try:
        size = int(size)
    except (TypeError, ValueError):
        return _error("Invalid size: must be 4 or 5")

    if size not in {4, 5}:
        return _error("Invalid size: must be 4 or 5")

    board = _generate_board(size)
    html = _build_html(board)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "board": board,
            "size": size,
            "html": html,
            "image": None,
        },
    }
