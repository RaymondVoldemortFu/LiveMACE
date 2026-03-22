import datetime as _dt

import requests


RDAP_URL = "https://rdap.org/domain/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_event(payload: dict, event_action: str):
    events = payload.get("events") or []
    for event in events:
        if event.get("eventAction") == event_action:
            return event.get("eventDate")
    return None


def _parse_date(value: str):
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")

    try:
        response = requests.get(f"{RDAP_URL}{domain}", timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Domain expiration lookup error: {exc}")
    except ValueError:
        return _error("Domain expiration service returned invalid JSON")

    expires_at = _parse_event(payload, "expiration")
    registered_at = _parse_event(payload, "registration")
    updated_at = _parse_event(payload, "last changed")

    expires_dt = _parse_date(expires_at)
    registered_dt = _parse_date(registered_at)
    age_days = None
    if registered_dt:
        age_days = ( _dt.datetime.now(_dt.timezone.utc) - registered_dt).days

    data = {
        "domain": domain,
        "expires_at": expires_at,
        "registered_at": registered_at,
        "updated_at": updated_at,
        "age_days": age_days,
    }
    return {"status": "ok", "error": None, "data": data}
