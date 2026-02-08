def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    char = params.get("char") or params.get("character") or params.get("code")
    if char is None or str(char).strip() == "":
        return _error("Missing required parameter: char or code")
    raw = str(char).strip()
    if len(raw) == 1:
        c = raw
        code = ord(c)
    else:
        try:
            if raw.startswith("U+") or raw.startswith("u+"):
                code = int(raw[2:].strip(), 16)
            else:
                code = int(raw)
            c = chr(code)
        except (ValueError, OverflowError):
            return _error("Invalid character or code point")
    if code > 0x10FFFF:
        return _error("Code point out of range")
    name = ""
    try:
        import unicodedata
        name = unicodedata.name(c, "")
    except Exception:
        pass
    data = {"char": c, "code_point": code, "hex": f"U+{code:04X}", "name": name}
    return {"status": "ok", "error": None, "data": data}
