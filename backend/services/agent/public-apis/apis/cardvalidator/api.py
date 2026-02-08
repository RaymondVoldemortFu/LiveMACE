from typing import Dict, Optional


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _clean_number(number: str) -> str:
    return "".join(ch for ch in number if ch.isdigit())


def _luhn_valid(number: str) -> bool:
    total = 0
    reverse_digits = list(map(int, number[::-1]))
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += digit
    return total % 10 == 0


def _match_card(number: str) -> Optional[Dict]:
    length = len(number)
    if number.startswith("4") and length in {13, 16, 19}:
        return {"niceType": "Visa", "type": "visa", "patterns": [4], "lengths": [13, 16, 19], "code": {"name": "CVV", "size": 3}}
    if length == 15 and number[:2] in {"34", "37"}:
        return {"niceType": "American Express", "type": "amex", "patterns": [34, 37], "lengths": [15], "code": {"name": "CID", "size": 4}}
    if length == 16 and (51 <= int(number[:2]) <= 55 or 2221 <= int(number[:4]) <= 2720):
        return {"niceType": "Mastercard", "type": "mastercard", "patterns": [51, 52, 53, 54, 55], "lengths": [16], "code": {"name": "CVC", "size": 3}}
    if length == 16 and (number.startswith("6011") or number.startswith("65") or 644 <= int(number[:3]) <= 649 or 622126 <= int(number[:6]) <= 622925):
        return {"niceType": "Discover", "type": "discover", "patterns": [6011, 65, 644, 645, 646, 647, 648, 649], "lengths": [16], "code": {"name": "CVV", "size": 3}}
    return None


def run(params: dict) -> dict:
    params = params or {}
    number = params.get("number")
    if not number or not isinstance(number, str):
        return _error("Missing required parameter: number")

    clean = _clean_number(number)
    if not clean:
        return _error("Invalid number: must contain digits")

    card = _match_card(clean)
    is_valid = _luhn_valid(clean)
    gaps = [4, 8, 12] if len(clean) >= 16 else [4, 10]

    card_info = {
        "niceType": card["niceType"] if card else "Unknown",
        "type": card["type"] if card else "unknown",
        "patterns": card["patterns"] if card else [],
        "gaps": gaps,
        "lengths": card["lengths"] if card else [len(clean)],
        "code": card["code"] if card else {"name": "CVV", "size": 3},
        "matchStrength": 1 if card else 0,
    }

    return {
        "status": "ok",
        "error": None,
        "data": {
            "card": card_info,
            "cardNumber": clean,
            "isValid": is_valid and (card is not None),
        },
    }
