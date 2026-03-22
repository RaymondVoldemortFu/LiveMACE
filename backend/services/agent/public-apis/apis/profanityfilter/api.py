import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    placeholder = params.get("placeholder") or "***"
    if text is None:
        return _error("Missing required parameter: text")
    text = str(text).strip()
    if not text:
        return {"status": "ok", "error": None, "data": {"original": text, "filtered": text, "changed": False}}

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Replace only profanity, vulgar, or offensive words in the following text with the placeholder \"***\". "
        "Keep all other words, punctuation, and casing unchanged. Output only the resulting text, no explanation."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        filtered = (response.choices[0].message.content or "").strip().strip('"')
    except Exception as exc:
        return _error(f"Profanity filter error: {exc}")

    data = {"original": text, "filtered": filtered, "changed": filtered != text}
    return {"status": "ok", "error": None, "data": data}
