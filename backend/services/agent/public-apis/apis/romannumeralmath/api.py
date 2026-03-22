ROMAN_VAL = [("M", 1000), ("CM", 900), ("D", 500), ("CD", 400), ("C", 100), ("XC", 90), ("L", 50), ("XL", 40), ("X", 10), ("IX", 9), ("V", 5), ("IV", 4), ("I", 1)]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _from_roman(s: str) -> int:
    s = s.upper().strip()
    d = dict(ROMAN_VAL)
    n = 0
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i : i + 2] in d:
            n += d[s[i : i + 2]]
            i += 2
        elif s[i] in d:
            n += d[s[i]]
            i += 1
        else:
            return -1
    return n


def _to_roman(n: int) -> str:
    if n <= 0 or n >= 4000:
        return ""
    out = []
    for sym, val in ROMAN_VAL:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


def run(params: dict) -> dict:
    params = params or {}
    a = params.get("a") or params.get("first")
    b = params.get("b") or params.get("second")
    operation = (params.get("operation") or params.get("op") or "add").strip().lower()
    if a is None or b is None:
        return _error("Missing required parameters: a, b")
    na = _from_roman(str(a).strip()) if str(a).strip().upper().isalpha() else int(str(a).strip())
    nb = _from_roman(str(b).strip()) if str(b).strip().upper().isalpha() else int(str(b).strip())
    if isinstance(na, int) and na < 0:
        return _error("Invalid first operand")
    if isinstance(nb, int) and nb < 0:
        return _error("Invalid second operand")
    if operation in ("add", "+"):
        result = na + nb
    elif operation in ("subtract", "sub", "-"):
        result = na - nb
    elif operation in ("multiply", "mul", "*"):
        result = na * nb
    else:
        return _error("operation must be add, subtract, or multiply")
    if result <= 0 or result >= 4000:
        roman_result = None
    else:
        roman_result = _to_roman(result)
    data = {"a": a, "b": b, "operation": operation, "numeric_result": result, "roman_result": roman_result}
    return {"status": "ok", "error": None, "data": data}
