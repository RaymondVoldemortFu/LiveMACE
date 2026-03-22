import datetime as dt


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    timestamp = params.get("timestamp") or params.get("date") or params.get("time")
    reference = params.get("reference") or params.get("base")
    if timestamp is None:
        return _error("Missing required parameter: timestamp or date")
    try:
        if isinstance(timestamp, (int, float)):
            t = dt.datetime.fromtimestamp(float(timestamp), tz=dt.timezone.utc)
        else:
            s = str(timestamp).strip().replace("Z", "+00:00")
            t = None
            for fmt, size in (("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
                try:
                    t = dt.datetime.strptime(s[:size], fmt).replace(tzinfo=dt.timezone.utc)
                    break
                except ValueError:
                    continue
            if t is None:
                return _error("Invalid timestamp format")
    except (ValueError, OSError):
        return _error("Invalid timestamp")
    ref = dt.datetime.now(dt.timezone.utc)
    if reference is not None:
        try:
            if isinstance(reference, (int, float)):
                ref = dt.datetime.fromtimestamp(float(reference), tz=dt.timezone.utc)
            else:
                ref = dt.datetime.strptime(str(reference).strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        except (ValueError, OSError):
            pass
    delta = t - ref
    secs = delta.total_seconds()
    if abs(secs) < 60:
        rel = "now" if abs(secs) < 2 else f"{int(secs)} seconds ago" if secs < 0 else f"in {int(secs)} seconds"
    elif abs(secs) < 3600:
        m = int(abs(secs) / 60)
        rel = f"{m} minutes ago" if secs < 0 else f"in {m} minutes"
    elif abs(secs) < 86400:
        h = int(abs(secs) / 3600)
        rel = f"{h} hours ago" if secs < 0 else f"in {h} hours"
    elif abs(secs) < 2592000:
        d = int(abs(secs) / 86400)
        rel = f"{d} days ago" if secs < 0 else f"in {d} days"
    elif abs(secs) < 31536000:
        mo = int(abs(secs) / 2592000)
        rel = f"{mo} months ago" if secs < 0 else f"in {mo} months"
    else:
        y = int(abs(secs) / 31536000)
        rel = f"{y} years ago" if secs < 0 else f"in {y} years"
    data = {"timestamp": timestamp, "reference": reference, "relative": rel, "seconds_ago": int(secs)}
    return {"status": "ok", "error": None, "data": data}
