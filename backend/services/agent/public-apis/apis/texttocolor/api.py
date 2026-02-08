import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()[:500]

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Based on the mood, theme, or meaning of this text, pick one main color as hex (e.g. #FF5733). "
        "Respond with a JSON object with keys: \"hex\", \"name\" (color name), \"reason\" (one short sentence)."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Text to color error: {exc}")

    hex_code = name = reason = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            hex_code = result.get("hex") or result.get("color") or ""
            name = result.get("name") or ""
            reason = result.get("reason") or ""
    except Exception:
        pass

    data = {"text": text[:100], "hex": hex_code, "name": name, "reason": reason}
    return {"status": "ok", "error": None, "data": data}
