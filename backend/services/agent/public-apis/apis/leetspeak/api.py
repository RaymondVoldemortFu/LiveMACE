def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


MAP = {
    "a": "4",
    "e": "3",
    "i": "1",
    "o": "0",
    "s": "5",
    "t": "7",
}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")

    out = []
    for ch in str(text):
        repl = MAP.get(ch.lower())
        out.append(repl if repl else ch)
    data = {"text": text, "leetspeak": "".join(out)}
    return {"status": "ok", "error": None, "data": data}
