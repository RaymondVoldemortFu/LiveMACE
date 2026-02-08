import re

import requests


DOH_URL = "https://dns.google/resolve"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _has_mx(domain: str) -> bool:
    response = requests.get(DOH_URL, params={"name": domain, "type": "MX"}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return bool(payload.get("Answer"))


def run(params: dict) -> dict:
    params = params or {}
    email = params.get("email")
    if not email or not isinstance(email, str):
        return _error("Missing required parameter: email")

    syntax_valid = bool(EMAIL_RE.match(email))
    domain = email.split("@")[-1].lower() if "@" in email else None
    mx_valid = False
    warnings = []
    if syntax_valid and domain:
        try:
            mx_valid = _has_mx(domain)
        except requests.RequestException as exc:
            warnings.append(f"MX lookup failed: {exc}")

    data = {
        "email": email,
        "syntax_valid": syntax_valid,
        "domain": domain,
        "mx_valid": mx_valid,
        "valid": syntax_valid and mx_valid,
        "warnings": warnings or None,
    }
    return {"status": "ok", "error": None, "data": data}
