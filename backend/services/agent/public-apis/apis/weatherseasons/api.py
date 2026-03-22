from zoneinfo import ZoneInfo
from datetime import datetime


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    lat = params.get("lat") or params.get("latitude")
    date = params.get("date")
    tz = params.get("timezone") or "UTC"
    if lat is None:
        return _error("Missing required parameter: lat")
    try:
        lat = float(lat)
    except (TypeError, ValueError):
        return _error("Invalid lat")
    try:
        now = datetime.now(ZoneInfo(str(tz)))
    except Exception:
        now = datetime.now(ZoneInfo("UTC"))
    if date:
        try:
            ds = str(date).strip()
            if len(ds) >= 7:
                month = int(ds[5:7])
            elif len(ds) >= 2:
                month = int(ds[:2])
            else:
                month = now.month
        except (ValueError, TypeError):
            month = now.month
    else:
        month = now.month
    if lat >= 0:
        seasons = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring", 6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}
    else:
        seasons = {12: "Summer", 1: "Summer", 2: "Summer", 3: "Autumn", 4: "Autumn", 5: "Autumn", 6: "Winter", 7: "Winter", 8: "Winter", 9: "Spring", 10: "Spring", 11: "Spring"}
    season = seasons.get(month, "Unknown")
    data = {"lat": lat, "month": month, "season": season, "hemisphere": "Northern" if lat >= 0 else "Southern"}
    return {"status": "ok", "error": None, "data": data}
