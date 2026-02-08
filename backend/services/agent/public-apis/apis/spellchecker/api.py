import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("content")
    if text is None:
        return _error("Missing required parameter: text")
    text = str(text).strip()
    if not text:
        return {"status": "ok", "error": None, "data": {"text": text, "corrected": text, "matches": [], "errors": []}}
    try:
        r = requests.post(
            "https://api.languagetool.org/v2/check",
            data={"text": text, "language": "en-US"},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"Spell check error: {e}")
    matches = j.get("matches") or []
    errors = [{"message": m.get("message"), "offset": m.get("offset"), "length": m.get("length"), "replacements": [r.get("value") for r in (m.get("replacements") or [])[:5]]} for m in matches]
    corrected = text
    for m in sorted(matches, key=lambda x: -x.get("offset", 0)):
        repl = (m.get("replacements") or [{}])[0].get("value")
        if repl:
            o, l = m.get("offset", 0), m.get("length", 0)
            corrected = corrected[:o] + repl + corrected[o + l:]
    data = {"text": text, "corrected": corrected, "matches": matches, "errors": errors}
    return {"status": "ok", "error": None, "data": data}
