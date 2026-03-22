def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


# Approximate: US men <-> EU. US 7 = 40 EU, 8 = 41, 9 = 42, 10 = 43, 11 = 44, 12 = 45
def _us_men_to_eu(us: float) -> float:
    return (us + 24) * 1.5 + 0.5


def _eu_to_us_men(eu: float) -> float:
    return (eu - 0.5) / 1.5 - 24


def run(params: dict) -> dict:
    params = params or {}
    value = params.get("value") or params.get("size")
    from_unit = str(params.get("from_unit") or params.get("from") or "us_men").strip().lower()
    to_unit = str(params.get("to_unit") or params.get("to") or "eu").strip().lower()
    if value is None:
        return _error("Missing required parameter: value")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _error("Invalid value")
    if from_unit in ("us", "us_men", "us_men"):
        eu = _us_men_to_eu(v)
        uk = v - 0.5
        data = {"value": v, "from_unit": "US men", "us_men": v, "eu": round(eu, 1), "uk": round(uk, 1)}
    elif from_unit in ("eu", "europe"):
        us = _eu_to_us_men(v)
        uk = us - 0.5
        data = {"value": v, "from_unit": "EU", "eu": v, "us_men": round(us, 1), "uk": round(uk, 1)}
    elif from_unit in ("uk", "uk_men"):
        us = v + 0.5
        eu = _us_men_to_eu(us)
        data = {"value": v, "from_unit": "UK", "uk": v, "us_men": round(us, 1), "eu": round(eu, 1)}
    else:
        return _error("from_unit must be us_men, eu, or uk")
    return {"status": "ok", "error": None, "data": data}
