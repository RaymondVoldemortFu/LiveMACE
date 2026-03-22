import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    topic = (params.get("topic") or "").strip() or None
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Give one short, memorable quote (1-2 sentences). It can be from a famous person or anonymous. "
        + (f"Theme or topic: {topic}. " if topic else "Any theme: wisdom, success, life, etc. ")
        + "Respond with a JSON object with keys: \"quote\" (the text), \"author\" (name or \"Unknown\"), \"topic\" (short label)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Random quote error: {exc}")

    quote = author = topic_out = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            quote = result.get("quote") or result.get("text") or ""
            author = result.get("author") or "Unknown"
            topic_out = result.get("topic") or ""
    except Exception:
        pass
    if not quote:
        quote = content

    data = {"quote": quote, "author": author, "topic": topic_out or topic}
    return {"status": "ok", "error": None, "data": data}
