import random
import string
from typing import List


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _build_card(size: int, free_space: bool) -> List[List[object]]:
    columns = []
    for col in range(size):
        start = col * 15 + 1
        end = start + 15
        numbers = random.sample(range(start, end), size)
        columns.append(numbers)

    card = [list(row) for row in zip(*columns)]
    if free_space and size % 2 == 1:
        center = size // 2
        card[center][center] = "FREE"
    return card


def _build_html(card: List[List[object]], headers: List[str]) -> str:
    head = "<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead>"
    body_rows = []
    for row in card:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        body_rows.append(f"<tr>{cells}</tr>")
    body = "<tbody>" + "".join(body_rows) + "</tbody></table>"
    return head + body


def _parse_bool(value: object, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    raise ValueError(
        f"Invalid {field_name}: must be a boolean or one of true/false/1/0/yes/no/on/off"
    )


def run(params: dict) -> dict:
    params = params or {}
    size = params.get("size", 5)
    free_space = params.get("freeSpace", True)

    try:
        size = int(size)
    except (TypeError, ValueError):
        return _error("Invalid size: must be an integer between 3 and 10")

    if size < 3 or size > 10:
        return _error("Invalid size: must be between 3 and 10")

    try:
        free_space = _parse_bool(free_space, field_name="freeSpace")
    except ValueError as exc:
        return _error(str(exc))

    card = _build_card(size, free_space)
    headers = list(string.ascii_uppercase[:size])
    html = _build_html(card, headers)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "card": card,
            "html": html,
            "size": size,
            "freeSpace": free_space,
            "totalCells": size * size,
            "winningPatterns": [
                "horizontal",
                "vertical",
                "diagonal",
                "four corners",
                "blackout (all cells)",
            ],
            "image": None,
        },
    }
