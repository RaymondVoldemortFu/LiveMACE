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
        "Tell one short, interesting, factual fact (1-2 sentences). "
        + (f"Topic: {topic}. " if topic else "Choose any topic: science, history, nature, or culture. ")
        + "Respond with a JSON object with keys: \"fact\" (the fact text) and \"topic\" (short topic label)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Random facts error: {exc}")

    fact = topic_out = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            fact = result.get("fact") or ""
            topic_out = result.get("topic") or ""
    except Exception:
        pass
    if not fact:
        fact = content

    data = {"fact": fact, "topic": topic_out or topic}
    return {"status": "ok", "error": None, "data": data}
