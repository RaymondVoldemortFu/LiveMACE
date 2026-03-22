import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()[:2000]

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Detect the language of this text. Respond with a JSON object with keys: "
        '"language" (ISO 639-1 code, e.g. en, zh), "name" (full language name), "confidence" (0-1).'
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Language detection error: {exc}")

    language = name = ""
    confidence = 0.5
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            language = result.get("language") or ""
            name = result.get("name") or ""
            confidence = float(result.get("confidence") or 0.5)
    except Exception:
        pass

    data = {"text": text[:100], "language": language, "name": name, "confidence": confidence}
    return {"status": "ok", "error": None, "data": data}
