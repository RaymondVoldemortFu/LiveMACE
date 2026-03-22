import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count") or params.get("n") or 5
    topic = (params.get("topic") or "").strip() or None
    try:
        count = int(count)
        count = max(1, min(50, count))
    except (TypeError, ValueError):
        count = 5

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"List exactly {count} different English words. "
        + (f"All related to or evoking: {topic}. " if topic else "Mix of nouns, verbs, adjectives; varied. ")
        + "One word per line, no numbers, no explanation. Respond with a JSON object with key \"words\" (array of strings)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Random words error: {exc}")

    words = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            words = result.get("words") or []
        if not isinstance(words, list):
            words = [w.strip() for w in content.split() if w.strip()][:count]
    except Exception:
        words = [w.strip() for w in content.replace(",", " ").split() if w.strip() and w.strip().isalpha()][:count]

    data = {"words": words[:count], "count": len(words[:count])}
    return {"status": "ok", "error": None, "data": data}
