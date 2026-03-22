import math
import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


SAFE_RE = re.compile(r"^[0-9\.\+\-\*\/\(\)\s\^\%]+$")


def run(params: dict) -> dict:
    params = params or {}
    expr = params.get("expression") or params.get("expr")
    if not expr or not isinstance(expr, str):
        return _error("Missing required parameter: expression")

    expr = expr.replace("^", "**")
    if not SAFE_RE.match(expr):
        return _error("Invalid expression")

    try:
        result = eval(expr, {"__builtins__": {}}, {"math": math})
    except Exception:
        return _error("Failed to evaluate expression")

    return {"status": "ok", "error": None, "data": {"expression": expr, "result": result}}
