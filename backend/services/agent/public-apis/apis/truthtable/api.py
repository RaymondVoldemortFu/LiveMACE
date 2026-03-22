import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_vars(expr: str) -> list:
    return sorted(set(re.findall(r"\b([A-Za-z])\b", expr)))


def _eval_expr(expr: str, vals: dict) -> bool:
    s = expr.upper()
    for k, v in vals.items():
        s = re.sub(rf"\b{k}\b", "1" if v else "0", s, flags=re.I)
    s = s.replace("AND", " and ").replace("OR", " or ").replace("NOT", " not ")
    s = s.replace("XOR", " ^ ")
    s = re.sub(r"\b1\b", "True", s)
    s = re.sub(r"\b0\b", "False", s)
    try:
        return bool(eval(s))
    except Exception:
        return False


def run(params: dict) -> dict:
    params = params or {}
    expression = params.get("expression") or params.get("expr") or params.get("formula")
    if expression is None or not str(expression).strip():
        return _error("Missing required parameter: expression")
    expr = str(expression).strip()
    vars_ = _parse_vars(expr)
    if not vars_:
        return _error("No variables found in expression (use single letters)")
    n = len(vars_)
    if n > 6:
        return _error("At most 6 variables supported")
    rows = []
    for i in range(2**n):
        vals = {vars_[j]: (i >> (n - 1 - j)) & 1 for j in range(n)}
        result = _eval_expr(expr, {k: bool(v) for k, v in vals.items()})
        row = {k: bool(vals[k]) for k in vars_}
        row["result"] = result
        rows.append(row)
    data = {"expression": expr, "variables": vars_, "table": rows}
    return {"status": "ok", "error": None, "data": data}
