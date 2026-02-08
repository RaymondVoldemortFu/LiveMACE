import random

import requests


RANDOM_USER_URL = "https://randomuser.me/api/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize_gender(gender: str) -> str:
    gender = gender.strip().lower()
    if gender not in {"male", "female"}:
        raise ValueError("Invalid gender. Allowed: male, female")
    return gender


def run(params: dict) -> dict:
    params = params or {}
    gender = params.get("gender")
    if not gender or not isinstance(gender, str):
        return _error("Missing required parameter: gender")
    try:
        gender = _normalize_gender(gender)
    except ValueError as exc:
        return _error(str(exc))

    count = params.get("count", 1)
    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count, expected integer")
    if count < 1 or count > 20:
        return _error("Count must be between 1 and 20")

    try:
        response = requests.get(
            RANDOM_USER_URL,
            params={"results": count * 2, "gender": gender},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Baby name service error: {exc}")
    except ValueError:
        return _error("Baby name service returned invalid JSON")

    results = payload.get("results") or []
    if len(results) < count:
        return _error("Baby name service returned insufficient results")

    names = []
    for idx in range(count):
        first = results[idx * 2]["name"]["first"]
        middle = results[idx * 2 + 1]["name"]["first"] if idx * 2 + 1 < len(results) else ""
        if not middle:
            middle = random.choice([r["name"]["first"] for r in results])
        full = f"{first} {middle}".strip()
        names.append({"firstName": first, "middleName": middle, "fullName": full})

    return {"status": "ok", "error": None, "data": {"count": count, "names": names}}
