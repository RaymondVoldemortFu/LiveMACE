import re


OUI = {
    "00:1A:2B": "ExampleCorp",
    "00:1B:63": "Apple",
    "00:1C:B3": "Cisco",
}

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    mac = params.get("mac")
    if not mac or not isinstance(mac, str):
        return _error("Missing required parameter: mac")
    if not MAC_RE.match(mac):
        return _error("Invalid MAC address")

    prefix = mac.upper().replace("-", ":")[:8]
    vendor = OUI.get(prefix)
    data = {"mac": mac, "vendor": vendor}
    return {"status": "ok", "error": None, "data": data}
