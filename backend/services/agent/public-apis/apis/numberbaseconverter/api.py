def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    number = params.get("number")
    from_base = params.get("from_base", 10)
    to_base = params.get("to_base", 16)
    try:
        from_base = int(from_base)
        to_base = int(to_base)
    except (TypeError, ValueError):
        return _error("from_base and to_base must be integers")
    if not (2 <= from_base <= 36 and 2 <= to_base <= 36):
        return _error("Bases must be between 2 and 36")
    if number is None or number == "":
        return _error("Missing required parameter: number")
    s = str(number).strip()
    try:
        n = int(s, from_base)
    except ValueError:
        return _error("Invalid number for the given base")
    if n < 0:
        return _error("Negative numbers not supported")
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n == 0:
        result = "0"
    else:
        out = []
        while n:
            out.append(digits[n % to_base])
            n //= to_base
        result = "".join(reversed(out))
    data = {"number": s, "from_base": from_base, "to_base": to_base, "result": result, "decimal": int(str(number).strip(), from_base)}
    return {"status": "ok", "error": None, "data": data}
