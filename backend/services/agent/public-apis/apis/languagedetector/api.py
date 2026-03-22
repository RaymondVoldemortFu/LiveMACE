import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Detect the language of the following text. Respond with a JSON object "
        "with keys: language (ISO 639-1 if possible) and confidence (0-1). "
        f"Text: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
    except Exception as exc:
        return _error(f"Language detector error: {exc}")

    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        language = result.get("language") if isinstance(result, dict) else None
        confidence = result.get("confidence") if isinstance(result, dict) else None
    except Exception:
        language = None
        confidence = None

    data = {"language": language, "confidence": confidence, "raw": content}
    return {"status": "ok", "error": None, "data": data}
