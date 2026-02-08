import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _valid(board, r, c, n):
    for i in range(9):
        if board[r][i] == n or board[i][c] == n:
            return False
    br, bc = 3 * (r // 3), 3 * (c // 3)
    for i in range(3):
        for j in range(3):
            if board[br + i][bc + j] == n:
                return False
    return True


def _solve(board):
    for r in range(9):
        for c in range(9):
            if board[r][c] == 0:
                for n in random.sample(range(1, 10), 9):
                    if _valid(board, r, c, n):
                        board[r][c] = n
                        if _solve(board):
                            return True
                        board[r][c] = 0
                return False
    return True


def _make_puzzle(difficulty=40):
    board = [[0] * 9 for _ in range(9)]
    _solve(board)
    cells = list(range(81))
    random.shuffle(cells)
    remove = min(60, max(30, difficulty))
    for i in cells[:remove]:
        board[i // 9][i % 9] = 0
    return board


def run(params: dict) -> dict:
    params = params or {}
    difficulty = params.get("difficulty") or 40
    try:
        difficulty = int(difficulty)
        difficulty = max(30, min(60, difficulty))
    except (TypeError, ValueError):
        difficulty = 40
    board = _make_puzzle(difficulty)
    flat = [board[r][c] for r in range(9) for c in range(9)]
    data = {"puzzle": board, "flat": flat, "difficulty": difficulty}
    return {"status": "ok", "error": None, "data": data}
