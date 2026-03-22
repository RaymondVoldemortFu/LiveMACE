import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _luhn_checksum(number: str) -> int:
    total = 0
    reverse_digits = number[::-1]
    for i, ch in enumerate(reverse_digits, start=1):
        digit = int(ch)
        if i % 2 == 1:
            total += digit
        else:
            doubled = digit * 2
            total += doubled - 9 if doubled > 9 else doubled
    return (10 - (total % 10)) % 10


def run(params: dict) -> dict:
    params = params or {}
    tac = params.get("tac")
    if tac:
        tac = str(tac)
        if not tac.isdigit() or len(tac) != 8:
            return _error("Invalid tac: must be 8 digits")
    else:
        tac = "".join(str(random.randint(0, 9)) for _ in range(8))

    serial = "".join(str(random.randint(0, 9)) for _ in range(6))
    partial = tac + serial
    check = _luhn_checksum(partial)
    imei = partial + str(check)

    data = {"imei": imei, "tac": tac, "serial": serial, "check_digit": check}
    return {"status": "ok", "error": None, "data": data}
