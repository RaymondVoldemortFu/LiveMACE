import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
    raise ValueError("Invalid JSON")


def _grid_html(grid: list, across: list, down: list) -> str:
    lines = []
    lines.append("<div class='crossword'>")
    lines.append("<pre>")
    for row in grid:
        line = []
        for cell in row:
            line.append(cell if cell else "#")
        lines.append("".join(line))
    lines.append("</pre>")
    lines.append("<div class='clues'>")
    lines.append("<strong>Across</strong>")
    for clue in across:
        lines.append(f"<div>{clue.get('number')}. {clue.get('clue')}</div>")
    lines.append("<strong>Down</strong>")
    for clue in down:
        lines.append(f"<div>{clue.get('number')}. {clue.get('clue')}</div>")
    lines.append("</div></div>")
    return "\n".join(lines)


def run(params: dict) -> dict:
    params = params or {}
    size = params.get("size")
    theme = params.get("theme")
    difficulty = params.get("difficulty")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    size_map = {"small": 10, "medium": 15, "large": 20}
    size_key = str(size or "medium").lower()
    grid_size = size_map.get(size_key, 15)
    theme = str(theme or "random")
    difficulty = str(difficulty or "medium")

    prompt = (
        "Generate a crossword puzzle. Return ONLY JSON with keys: "
        "size (number), difficulty (string), theme (string), "
        "grid (2D array with letters or null), across (array), down (array). "
        "Each clue item: number, clue, answer, length. "
        f"Grid size: {grid_size}. Theme: {theme}. Difficulty: {difficulty}. "
        "Use uppercase letters for answers. Ensure answers fit the grid."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        content = response.choices[0].message.content or ""
        payload = _extract_json(content)
    except Exception as exc:
        return _error(f"Crossword generator error: {exc}")

    if not isinstance(payload, dict):
        return _error("Crossword generator returned invalid response")

    grid = payload.get("grid")
    across = payload.get("across") or []
    down = payload.get("down") or []

    if not isinstance(grid, list) or len(grid) != grid_size:
        return _error("Crossword generator returned invalid grid")

    word_count = len(across) + len(down)
    html = _grid_html(grid, across, down)
    data = {
        "size": grid_size,
        "difficulty": difficulty,
        "theme": theme,
        "grid": grid,
        "across": across,
        "down": down,
        "wordCount": word_count,
        "html": html,
        "image": None,
        "solutionImage": None,
    }
    return {"status": "ok", "error": None, "data": data}
