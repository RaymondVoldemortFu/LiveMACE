import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("content")
    target = params.get("target") or params.get("to") or "en"
    source = params.get("source") or params.get("from")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()[:3000]

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Translate the following text to {target}. "
        + (f"Source language is {source}. " if source else "Detect the source language if not obvious. ")
        + "Output only the translation, no explanation or quotes."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        translated = (response.choices[0].message.content or "").strip().strip('"')
    except Exception as exc:
        return _error(f"Translation error: {exc}")

    data = {"text": text[:200], "translated": translated, "source": source or "auto", "target": target}
    return {"status": "ok", "error": None, "data": data}
