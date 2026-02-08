from datetime import datetime, timezone


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    action = (params.get("action") or params.get("mode") or "current").strip().lower()
    timestamp = params.get("timestamp") or params.get("ts")
    if action in ("to_date", "decode", "convert") and timestamp is not None:
        try:
            ts = int(float(timestamp))
            if ts > 1e12:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            data = {"timestamp": ts, "iso8601": dt.isoformat(), "date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M:%S")}
            return {"status": "ok", "error": None, "data": data}
        except (ValueError, OSError):
            return _error("Invalid timestamp")
    if params.get("date") or params.get("datetime"):
        s = str(params.get("date") or params.get("datetime")).strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp())
                data = {"timestamp": ts, "iso8601": dt.isoformat()}
                return {"status": "ok", "error": None, "data": data}
            except ValueError:
                continue
        return _error("Invalid date format")
    now = datetime.now(timezone.utc)
    data = {"timestamp": int(now.timestamp()), "iso8601": now.isoformat(), "milliseconds": int(now.timestamp() * 1000)}
    return {"status": "ok", "error": None, "data": data}
