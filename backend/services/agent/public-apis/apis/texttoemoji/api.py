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
        "Replace words or phrases in this text with appropriate emoji where it makes sense. "
        "Keep the text readable; you can mix words and emoji. Output only the result, no explanation."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        result = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Text to emoji error: {exc}")

    data = {"original": text, "result": result}
    return {"status": "ok", "error": None, "data": data}
