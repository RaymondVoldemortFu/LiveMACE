from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    length = params.get("length") or "medium"
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()[:8000]

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Summarize the following text in a {length} length (1-2 sentences for short, a short paragraph for medium, a few sentences for long). Output only the summary."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        summary = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Summarizer error: {exc}")

    data = {"original_length": len(text), "summary": summary, "length": length}
    return {"status": "ok", "error": None, "data": data}
