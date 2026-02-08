import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    style = params.get("style") or "formal"
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Rewrite the following text in a {style} style. Keep the same meaning and length roughly. Output only the rewritten text."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        result = (response.choices[0].message.content or "").strip().strip('"')
    except Exception as exc:
        return _error(f"Text styler error: {exc}")

    data = {"original": text, "style": style, "result": result}
    return {"status": "ok", "error": None, "data": data}
