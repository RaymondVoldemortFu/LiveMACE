import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word")
    limit = params.get("limit") or 15
    if not word or not str(word).strip():
        return _error("Missing required parameter: word")
    word = str(word).strip()
    try:
        limit = min(50, max(1, int(limit)))
    except (TypeError, ValueError):
        limit = 15

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"List {limit} English words that rhyme with \"{word}\". "
        'Respond with a JSON object with key "rhymes" (array of strings).'
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Rhymes error: {exc}")

    rhymes = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            rhymes = result.get("rhymes") or result.get("words") or []
    except Exception:
        pass
    data = {"word": word, "rhymes": rhymes[:limit]}
    return {"status": "ok", "error": None, "data": data}
