import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("sentence")
    tense = params.get("tense") or "past"
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Convert the following sentence to {tense} tense. Keep the same meaning and subject. "
        "Output only the converted sentence, no explanation."
        f"\n\nSentence: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        converted = (response.choices[0].message.content or "").strip().strip('"')
    except Exception as exc:
        return _error(f"Tense converter error: {exc}")

    data = {"original": text, "tense": tense, "converted": converted}
    return {"status": "ok", "error": None, "data": data}
