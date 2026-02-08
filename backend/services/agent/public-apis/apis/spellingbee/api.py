import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word")
    letters = params.get("letters") or params.get("required_letter") or ""
    if word is None or not str(word).strip():
        return _error("Missing required parameter: word")
    word = str(word).strip().upper()
    letters = str(letters).strip().upper().replace(" ", "")
    if not letters:
        return _error("Missing required parameter: letters (e.g. center letter + outer letters)")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"In Spelling Bee, the word must use only the letters in: {letters}, and must include the center letter (the first letter: {letters[0] if letters else '?'}). "
        f"Is '{word}' a valid English word that fits these rules? "
        "Respond with a JSON object with keys: \"valid\" (boolean), \"reason\" (one short sentence)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Spelling bee check error: {exc}")

    valid = False
    reason = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            valid = bool(result.get("valid"))
            reason = str(result.get("reason") or "")
    except Exception:
        pass

    data = {"word": word, "letters": letters, "valid": valid, "reason": reason}
    return {"status": "ok", "error": None, "data": data}
