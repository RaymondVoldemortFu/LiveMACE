import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _neighbors(x, y, w, h):
    dirs = [(2, 0), (-2, 0), (0, 2), (0, -2)]
    random.shuffle(dirs)
    for dx, dy in dirs:
        nx, ny = x + dx, y + dy
        if 0 < nx < w - 1 and 0 < ny < h - 1:
            yield nx, ny, dx // 2, dy // 2


def run(params: dict) -> dict:
    params = params or {}
    width = params.get("width", 15)
    height = params.get("height", 15)
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return _error("Invalid width/height")
    width = max(5, width | 1)
    height = max(5, height | 1)

    grid = [["#"] * width for _ in range(height)]
    stack = [(1, 1)]
    grid[1][1] = " "
    while stack:
        x, y = stack[-1]
        moves = list(_neighbors(x, y, width, height))
        moved = False
        for nx, ny, wx, wy in moves:
            if grid[ny][nx] == "#":
                grid[y + wy][x + wx] = " "
                grid[ny][nx] = " "
                stack.append((nx, ny))
                moved = True
                break
        if not moved:
            stack.pop()

    data = {"width": width, "height": height, "maze": ["".join(row) for row in grid]}
    return {"status": "ok", "error": None, "data": data}
import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _carve(grid, x, y, width, height):
    directions = [(2, 0), (-2, 0), (0, 2), (0, -2)]
    random.shuffle(directions)
    for dx, dy in directions:
        nx, ny = x + dx, y + dy
        if 0 < nx < width - 1 and 0 < ny < height - 1 and grid[ny][nx] == "#":
            grid[ny - dy // 2][nx - dx // 2] = " "
            grid[ny][nx] = " "
            _carve(grid, nx, ny, width, height)


def run(params: dict) -> dict:
    params = params or {}
    width = params.get("width", 15)
    height = params.get("height", 15)

    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        return _error("Invalid width/height")

    if width < 5 or height < 5:
        return _error("Width/height must be >= 5")
    if width % 2 == 0:
        width += 1
    if height % 2 == 0:
        height += 1

    grid = [["#"] * width for _ in range(height)]
    grid[1][1] = " "
    _carve(grid, 1, 1, width, height)
    grid[0][1] = " "
    grid[height - 1][width - 2] = " "

    maze = ["".join(row) for row in grid]
    data = {"width": width, "height": height, "maze": maze}
    return {"status": "ok", "error": None, "data": data}
