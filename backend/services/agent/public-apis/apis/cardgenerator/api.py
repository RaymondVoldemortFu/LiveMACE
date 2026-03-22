import random
import uuid
from typing import List, Tuple


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _luhn_check_digit(number: str) -> str:
    total = 0
    reverse_digits = list(map(int, number[::-1]))
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 0:
            total += digit
        else:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
    return str((10 - (total % 10)) % 10)


def _generate_number(prefix: str, length: int) -> str:
    body_length = length - len(prefix) - 1
    body = "".join(str(random.randint(0, 9)) for _ in range(body_length))
    partial = prefix + body
    check = _luhn_check_digit(partial)
    return partial + check


def _pick_prefix(brand: str) -> Tuple[str, int]:
    if brand == "visa":
        return "4", 16
    if brand == "mastercard":
        if random.random() < 0.5:
            return str(random.randint(51, 55)), 16
        return str(random.randint(2221, 2720)), 16
    if brand == "amex":
        return random.choice(["34", "37"]), 15
    if brand == "discover":
        prefixes = ["6011", "65"] + [str(i) for i in range(644, 650)]
        if random.random() < 0.5:
            return random.choice(prefixes), 16
        return str(random.randint(622126, 622925)), 16
    raise ValueError("Unsupported card brand")


def _format_number(number: str) -> dict:
    masked = "*" * (len(number) - 4) + number[-4:]
    grouped = " ".join(number[i : i + 4] for i in range(0, len(number), 4))
    return {"masked": masked, "unmasked": grouped, "last4": number[-4:]}


def _generate_cards(brand: str, count: int) -> List[dict]:
    cards = []
    for _ in range(count):
        prefix, length = _pick_prefix(brand)
        number = _generate_number(prefix, length)
        cards.append(
            {
                "cvv": random.randint(100, 999) if brand != "amex" else random.randint(1000, 9999),
                "issuer": "TEST BANK",
                "id": str(uuid.uuid4()),
                "number": number,
                "expiration": "12/2030",
                "brand": brand,
                "number_alt": _format_number(number),
            }
        )
    return cards


def run(params: dict) -> dict:
    params = params or {}
    brand = params.get("brand")
    count = params.get("count", 1)

    if not brand or not isinstance(brand, str):
        return _error("Missing required parameter: brand")

    brand = brand.strip().lower()
    if brand not in {"visa", "mastercard", "amex", "discover"}:
        return _error("Invalid brand: must be visa, mastercard, amex, or discover")

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer between 1 and 20")

    if count < 1 or count > 20:
        return _error("Invalid count: must be between 1 and 20")

    cards = _generate_cards(brand, count)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "brand": brand,
            "count": count,
            "cards": cards,
            "owner": {
                "name": "Test User",
                "address": {
                    "street": "123 Test Street",
                    "city": "Test City",
                    "state": "Test State",
                    "zipCode": "00000",
                },
            },
        },
    }
