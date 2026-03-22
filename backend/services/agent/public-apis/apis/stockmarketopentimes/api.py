from zoneinfo import ZoneInfo


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    timezone = params.get("timezone") or params.get("tz") or "America/New_York"
    exchange = (params.get("exchange") or "NYSE").strip().upper()
    try:
        tz = ZoneInfo(str(timezone))
    except Exception:
        tz = ZoneInfo("America/New_York")
    open_utc = (9, 30)
    close_utc = (16, 0)
    if exchange in ("NASDAQ", "NYSE", "US"):
        open_local = "9:30 AM"
        close_local = "4:00 PM"
        open_utc = (9, 30)
        close_utc = (16, 0)
    else:
        open_local = "9:30 AM"
        close_local = "4:00 PM"
    data = {
        "exchange": exchange,
        "timezone": str(tz),
        "open_local": open_local,
        "close_local": close_local,
        "open_utc": f"{open_utc[0]:02d}:{open_utc[1]:02d}",
        "close_utc": f"{close_utc[0]:02d}:{close_utc[1]:02d}",
        "note": "US market hours (ET); other exchanges may vary",
    }
    return {"status": "ok", "error": None, "data": data}
