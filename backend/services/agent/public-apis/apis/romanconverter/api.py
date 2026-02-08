ROMAN_VAL = [("M", 1000), ("CM", 900), ("D", 500), ("CD", 400), ("C", 100), ("XC", 90), ("L", 50), ("XL", 40), ("X", 10), ("IX", 9), ("V", 5), ("IV", 4), ("I", 1)]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _to_roman(n: int) -> str:
    if n <= 0 or n >= 4000:
        return ""
    out = []
    for sym, val in ROMAN_VAL:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


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


def run(params: dict) -> dict:
    params = params or {}
    value = params.get("value") or params.get("number") or params.get("input")
    if value is None or value == "":
        return _error("Missing required parameter: value")
    v = str(value).strip()
    if v.isdigit():
        n = int(v)
        if not 1 <= n <= 3999:
            return _error("Number must be 1-3999")
        roman = _to_roman(n)
        data = {"input": v, "type": "number", "number": n, "roman": roman}
    else:
        n = _from_roman(v)
        if n < 0:
            return _error("Invalid Roman numeral")
        data = {"input": v, "type": "roman", "roman": v, "number": n}
    return {"status": "ok", "error": None, "data": data}
