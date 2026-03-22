import re

import requests


BGPVIEW_URL = "https://api.bgpview.io/asn/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize_asn(asn: str) -> str:
    asn = asn.strip().upper()
    if not asn.startswith("AS"):
        asn = f"AS{asn}"
    if not re.fullmatch(r"AS\\d+", asn):
        raise ValueError("Invalid ASN format")
    return asn


def run(params: dict) -> dict:
    asn = (params or {}).get("asn")
    if not asn or not isinstance(asn, str):
        return _error("Missing required parameter: asn")
    try:
        normalized = _normalize_asn(asn)
    except ValueError as exc:
        return _error(str(exc))

    try:
        response = requests.get(f"{BGPVIEW_URL}{normalized[2:]}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"ASN lookup service error: {exc}")
    except ValueError:
        return _error("ASN lookup service returned invalid JSON")

    data = payload.get("data") or {}
    if not data:
        return _error("ASN not found")

    return {
        "status": "ok",
        "error": None,
        "data": {
            "asn": normalized,
            "simple": normalized[2:],
            "handle": data.get("name") or normalized,
            "description": data.get("description_short") or data.get("description") or "",
        },
    }
