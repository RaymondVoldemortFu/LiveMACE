import random
import string


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    words = params.get("words") or params.get("word_list")
    size = params.get("size") or 10
    if words is None:
        return _error("Missing required parameter: words (list of words)")
    if isinstance(words, str):
        words = [w.strip().upper() for w in words.split() if w.strip()]
    else:
        words = [str(w).strip().upper() for w in words if str(w).strip()]
    if not words:
        return _error("No valid words provided")
    try:
        size = int(size)
        size = max(5, min(20, size))
    except (TypeError, ValueError):
        size = 10
    grid = [["" for _ in range(size)] for _ in range(size)]
    for word in words:
        if len(word) > size:
            continue
        placed = False
        for _ in range(50):
            row = random.randint(0, size - 1)
            col = random.randint(0, size - 1)
            dx = random.choice([-1, 0, 1])
            dy = random.choice([-1, 0, 1])
            if dx == 0 and dy == 0:
                continue
            if col + (len(word) - 1) * dx < 0 or col + (len(word) - 1) * dx >= size:
                continue
            if row + (len(word) - 1) * dy < 0 or row + (len(word) - 1) * dy >= size:
                continue
            ok = True
            for i, c in enumerate(word):
                r, c_ = row + i * dy, col + i * dx
                if grid[r][c_] and grid[r][c_] != c:
                    ok = False
                    break
            if ok:
                for i, c in enumerate(word):
                    grid[row + i * dy][col + i * dx] = c
                placed = True
                break
    for r in range(size):
        for c in range(size):
            if not grid[r][c]:
                grid[r][c] = random.choice(string.ascii_uppercase)
    data = {"grid": grid, "words": words, "size": size}
    return {"status": "ok", "error": None, "data": data}
