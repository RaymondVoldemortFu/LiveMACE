import socket
import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not str(domain).strip():
        return _error("Missing required parameter: domain")
    domain = str(domain).strip().lower().rstrip(".")
    if "://" in domain:
        domain = domain.split("://", 1)[1].split("/", 1)[0]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(("whois.iana.org", 43))
        s.send((domain + "\r\n").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        raw = data.decode("utf-8", errors="replace")
    except (socket.timeout, socket.error, OSError) as e:
        return _error(str(e))
    parsed = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" in line and not line.startswith("%"):
            k, _, v = line.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k and v:
                parsed.setdefault(k, []).append(v)
    result = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    data = {"domain": domain, "raw": raw[:5000], "parsed": result}
    return {"status": "ok", "error": None, "data": data}
