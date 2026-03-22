import ipaddress

import requests


DOH_URL = "https://dns.google/resolve"
DEFAULT_RBLS = [
    "zen.spamhaus.org",
    "bl.spamcop.net",
    "dnsbl.sorbs.net",
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _reverse_ip(ip: str) -> str:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv4Address):
        return ".".join(reversed(ip.split(".")))
    raise ValueError("IPv6 not supported for RBL")


def _is_listed(ip: str, rbl: str) -> bool:
    query = f"{_reverse_ip(ip)}.{rbl}"
    response = requests.get(DOH_URL, params={"name": query, "type": "A"}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return bool(payload.get("Answer"))


def run(params: dict) -> dict:
    params = params or {}
    ip = params.get("ip")
    rbllist = params.get("rbls") or DEFAULT_RBLS
    if not ip or not isinstance(ip, str):
        return _error("Missing required parameter: ip")

    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return _error("Invalid IP address")

    if isinstance(rbllist, str):
        rbllist = [r.strip() for r in rbllist.split(",") if r.strip()]

    results = []
    for rbl in rbllist:
        try:
            listed = _is_listed(ip, rbl)
        except requests.RequestException:
            listed = False
        except ValueError as exc:
            return _error(str(exc))
        results.append({"rbl": rbl, "listed": listed})

    data = {"ip": ip, "results": results, "listed": any(r["listed"] for r in results)}
    return {"status": "ok", "error": None, "data": data}
