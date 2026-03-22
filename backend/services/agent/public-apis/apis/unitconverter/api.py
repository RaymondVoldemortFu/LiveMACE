def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


# Common conversions: to base unit (SI or canonical)
RATES = {
    ("length", "m", "km"): 0.001,
    ("length", "m", "cm"): 100,
    ("length", "m", "mm"): 1000,
    ("length", "m", "mi"): 1 / 1609.344,
    ("length", "m", "yd"): 1 / 0.9144,
    ("length", "m", "ft"): 1 / 0.3048,
    ("length", "m", "in"): 1 / 0.0254,
    ("mass", "kg", "g"): 1000,
    ("mass", "kg", "lb"): 2.20462262,
    ("mass", "kg", "oz"): 35.2739619,
    ("temp", "c", "f"): lambda c: c * 9 / 5 + 32,
    ("temp", "f", "c"): lambda f: (f - 32) * 5 / 9,
}


def _convert(value: float, from_u: str, to_u: str, category: str) -> float:
    from_u = from_u.lower().strip()
    to_u = to_u.lower().strip()
    if from_u == to_u:
        return value
    if category == "temp":
        if from_u == "c" and to_u == "f":
            return (value * 9 / 5) + 32
        if from_u == "f" and to_u == "c":
            return (value - 32) * 5 / 9
        return value
    to_base = {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "mi": 1609.344, "yd": 0.9144, "ft": 0.3048, "in": 0.0254}
    from_base = {"kg": 1, "g": 0.001, "lb": 0.453592, "oz": 0.0283495}
    if category == "length" and from_u in to_base and to_u in to_base:
        return value * to_base[from_u] / to_base[to_u]
    if category == "mass" and from_u in from_base and to_u in from_base:
        return value * from_base[from_u] / from_base[to_u]
    return value


def run(params: dict) -> dict:
    params = params or {}
    value = params.get("value") or params.get("amount")
    from_unit = params.get("from") or params.get("from_unit")
    to_unit = params.get("to") or params.get("to_unit")
    category = (params.get("category") or "length").strip().lower()
    if value is None or from_unit is None or to_unit is None:
        return _error("Missing required parameters: value, from, to")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return _error("Invalid value")
    from_u = str(from_unit).strip()
    to_u = str(to_unit).strip()
    result = _convert(value, from_u, to_u, category)
    data = {"value": value, "from_unit": from_u, "to_unit": to_u, "result": round(result, 10), "category": category}
    return {"status": "ok", "error": None, "data": data}
