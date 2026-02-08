MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
    "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
    "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
    "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..", "0": "-----",
    "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....",
    "7": "--...", "8": "---..", "9": "----.", " ": "/",
}
REV = {v: k for k, v in MORSE.items()}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    mode = (params.get("mode") or "encode").strip().lower()
    if text is None or text == "":
        return _error("Missing required parameter: text")
    text = str(text).strip()
    if mode == "decode":
        parts = text.replace("   ", " / ").split()
        out = "".join(REV.get(p, "?") for p in parts)
        data = {"text": text, "result": out, "mode": "decode"}
    else:
        out = " ".join(MORSE.get(c.upper(), "?") for c in text)
        data = {"text": text, "result": out, "mode": "encode"}
    return {"status": "ok", "error": None, "data": data}
