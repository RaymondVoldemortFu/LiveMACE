import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    ua = params.get("user_agent") or params.get("ua") or params.get("useragent")
    if ua is None or not str(ua).strip():
        return _error("Missing required parameter: user_agent")
    s = str(ua).strip()
    browser = "Unknown"
    if "Chrome" in s and "Edg" not in s:
        m = re.search(r"Chrome/([\d.]+)", s)
        browser = f"Chrome {m.group(1)}" if m else "Chrome"
    elif "Firefox" in s:
        m = re.search(r"Firefox/([\d.]+)", s)
        browser = f"Firefox {m.group(1)}" if m else "Firefox"
    elif "Safari" in s and "Chrome" not in s:
        m = re.search(r"Version/([\d.]+)", s)
        browser = f"Safari {m.group(1)}" if m else "Safari"
    elif "Edg" in s:
        m = re.search(r"Edg/([\d.]+)", s)
        browser = f"Edge {m.group(1)}" if m else "Edge"
    os_name = "Unknown"
    if "Windows NT 10" in s:
        os_name = "Windows 10/11"
    elif "Windows" in s:
        os_name = "Windows"
    elif "Mac OS X" in s:
        os_name = "macOS"
    elif "Linux" in s:
        os_name = "Linux"
    elif "Android" in s:
        os_name = "Android"
    elif "iPhone" in s or "iPad" in s:
        os_name = "iOS"
    data = {"user_agent": s, "browser": browser, "os": os_name}
    return {"status": "ok", "error": None, "data": data}
