from functools import lru_cache

import requests


DISPOSABLE_LIST_URL = (
    "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/master/disposable_email_blocklist.conf"
)


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


@lru_cache(maxsize=1)
def _load_blocklist() -> set:
    response = requests.get(DISPOSABLE_LIST_URL, timeout=20)
    response.raise_for_status()
    lines = [line.strip().lower() for line in response.text.splitlines() if line.strip()]
    return set(lines)


def run(params: dict) -> dict:
    params = params or {}
    email = params.get("email")
    if not email or not isinstance(email, str) or "@" not in email:
        return _error("Missing required parameter: email")

    domain = email.split("@")[-1].lower()
    try:
        blocklist = _load_blocklist()
    except requests.RequestException as exc:
        return _error(f"Disposable domain list error: {exc}")

    data = {
        "email": email,
        "domain": domain,
        "disposable": domain in blocklist,
        "source": "disposable-email-domains",
    }
    return {"status": "ok", "error": None, "data": data}
