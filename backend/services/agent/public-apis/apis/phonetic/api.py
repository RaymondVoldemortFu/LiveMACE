def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _soundex(s: str) -> str:
    if not s or not s.isalpha():
        return ""
    s = s.upper()
    first = s[0]
    mapping = {"B": "1", "F": "1", "P": "1", "V": "1", "C": "2", "G": "2", "J": "2", "K": "2", "Q": "2", "S": "2", "X": "2", "Z": "2",
              "D": "3", "T": "3", "L": "4", "M": "5", "N": "5", "R": "6"}
    result = [first]
    for c in s[1:]:
        code = mapping.get(c)
        if code and code != (result[-1] if result[-1].isdigit() else None):
            result.append(code)
    result = [c for c in result if c.isdigit() or c == first]
    result = [first] + [c for c in result[1:] if c.isdigit()]
    while len(result) < 4:
        result.append("0")
    return "".join(result)[:4]


def _metaphone_simple(s: str) -> str:
    if not s or not s.isalpha():
        return ""
    s = s.upper()
    result = []
    i = 0
    drop = {"A", "E", "I", "O", "U", "H", "W", "Y"}
    while i < len(s) and len(result) < 6:
        c = s[i]
        if c in drop and i > 0:
            i += 1
            continue
        if c == "C":
            if i + 1 < len(s) and s[i + 1] in "EIY":
                result.append("S")
            elif i + 1 < len(s) and s[i + 1] == "H":
                result.append("X")
                i += 1
            else:
                result.append("K")
        elif c == "G" and i + 1 < len(s) and s[i + 1] in "EIY":
            result.append("J")
        elif c == "P" and i + 1 < len(s) and s[i + 1] == "H":
            result.append("F")
            i += 1
        elif c == "Q":
            result.append("K")
        elif c == "X":
            result.append("KS")
        elif c == "Z":
            result.append("S")
        elif c in "AEIOU" and i == 0:
            result.append(c)
        elif c not in drop:
            result.append(c)
        i += 1
    return "".join(result)[:6] if result else ""


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("word")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text or word")
    word = str(text).strip().split()[0] if str(text).strip() else ""
    if not word or not word.isalpha():
        return _error("Provide a single word")
    data = {"word": word, "soundex": _soundex(word), "metaphone": _metaphone_simple(word)}
    return {"status": "ok", "error": None, "data": data}
