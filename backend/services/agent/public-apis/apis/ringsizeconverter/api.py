def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


# Approximate conversions: US number <-> circumference (mm). US 5 ≈ 49.3mm, 6 ≈ 51.9, 7 ≈ 54.4, 8 ≈ 57.0, 9 ≈ 59.5, 10 ≈ 62.1
def _us_to_mm(us: float) -> float:
    return 40.0 + us * 2.55


def _mm_to_us(mm: float) -> float:
    return (mm - 40.0) / 2.55


def run(params: dict) -> dict:
    params = params or {}
    value = params.get("value") or params.get("size")
    from_unit = str(params.get("from_unit") or params.get("from") or "us").strip().lower()
    to_unit = str(params.get("to_unit") or params.get("to") or "mm").strip().lower()
    if value is None:
        return _error("Missing required parameter: value")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _error("Invalid value")
    if from_unit in ("us", "usa", "us_number"):
        mm = _us_to_mm(v)
        if to_unit in ("uk", "eu"):
            uk = round(v - 0.5, 1)
            data = {"value": v, "from_unit": "US", "us": v, "mm": round(mm, 2), "uk": uk}
        else:
            data = {"value": v, "from_unit": "US", "us": v, "mm": round(mm, 2)}
    elif from_unit in ("mm", "circumference"):
        us = _mm_to_us(v)
        uk = round(us - 0.5, 1)
        data = {"value": v, "from_unit": "mm", "mm": v, "us": round(us, 2), "uk": uk}
    else:
        return _error("from_unit must be us or mm")
    return {"status": "ok", "error": None, "data": data}
