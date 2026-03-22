import json
import threading
from datetime import datetime
from pathlib import Path


_LOCK = threading.Lock()
_STORE_PATH = Path("/tmp/apiverve_counter.json")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _load_store() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_store(store: dict) -> None:
    _STORE_PATH.write_text(json.dumps(store))


def _number_to_words(num: int) -> str:
    if num == 0:
        return "zero"

    ones = [
        "",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    ]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    def _under_1000(n: int) -> str:
        if n == 0:
            return ""
        if n < 20:
            return ones[n]
        if n < 100:
            return tens[n // 10] + ("" if n % 10 == 0 else f"-{ones[n % 10]}")
        rest = _under_1000(n % 100)
        return ones[n // 100] + " hundred" + ("" if not rest else f" {rest}")

    parts = []
    millions = num // 1_000_000
    thousands = (num % 1_000_000) // 1_000
    remainder = num % 1_000
    if millions:
        parts.append(f"{_under_1000(millions)} million")
    if thousands:
        parts.append(f"{_under_1000(thousands)} thousand")
    if remainder:
        parts.append(_under_1000(remainder))
    return " ".join(part for part in parts if part).strip()


def _number_to_ordinal_words(num: int) -> str:
    special = {
        "one": "first",
        "two": "second",
        "three": "third",
        "five": "fifth",
        "eight": "eighth",
        "nine": "ninth",
        "twelve": "twelfth",
    }
    words = _number_to_words(num)
    if words == "zero":
        return "zeroth"
    last = words.split()[-1]
    if "-" in last:
        base, suffix = last.split("-")
        last_word = special.get(suffix, suffix + "th")
        return words.rsplit(" ", 1)[0] + f" {base}-{last_word}"
    last_word = special.get(last, last + "th")
    return words.rsplit(" ", 1)[0] + (" " if " " in words else "") + last_word


def run(params: dict) -> dict:
    params = params or {}
    counter_id = params.get("id")
    action = params.get("action")
    value = params.get("value")

    if action == "list":
        with _LOCK:
            store = _load_store()
        counters = []
        for key, entry in store.items():
            counters.append({"id": key, "value": entry.get("value", 0), "lastUpdated": entry.get("lastUpdated")})
        return {"status": "ok", "error": None, "data": {"counters": counters}}

    if not counter_id or not isinstance(counter_id, str):
        return _error("Missing required parameter: id")

    action = str(action or "get").lower()
    if action not in {"get", "increment", "decrement", "reset", "delete"}:
        return _error("Invalid action: must be get, increment, decrement, reset, or delete")

    try:
        if value is not None:
            value = int(value)
    except (TypeError, ValueError):
        return _error("Invalid value: must be an integer")

    with _LOCK:
        store = _load_store()
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        entry = store.get(counter_id)
        if entry is None:
            entry = {"created": now, "value": 0}
            store[counter_id] = entry

        if action == "delete":
            store.pop(counter_id, None)
            _save_store(store)
            return {"status": "ok", "error": None, "data": {"deleted": True, "id": counter_id}}

        if action == "reset":
            entry["value"] = 0
        elif action == "increment":
            entry["value"] = int(entry.get("value", 0)) + (value if value is not None else 1)
        elif action == "decrement":
            entry["value"] = int(entry.get("value", 0)) - (value if value is not None else 1)
        elif value is not None and action == "get":
            entry["value"] = value

        entry["lastUpdated"] = now
        entry["lastAction"] = action
        store[counter_id] = entry
        _save_store(store)

    current = int(entry.get("value", 0))
    return {
        "status": "ok",
        "error": None,
        "data": {
            "created": entry.get("created"),
            "id": counter_id,
            "lastAction": action,
            "lastRead": now,
            "lastUpdated": entry.get("lastUpdated"),
            "numberOfDigits": len(str(abs(current))),
            "ordinal": _number_to_ordinal_words(current),
            "value": current,
            "words": _number_to_words(current),
        },
    }
