import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    pattern = params.get("pattern") or params.get("word") or "_____"
    exclude = params.get("exclude") or params.get("wrong") or ""
    include = params.get("include") or params.get("correct_letters") or ""
    if not pattern or not str(pattern).strip():
        return _error("Missing required parameter: pattern (e.g. s__t_ or _____)")
    pattern = str(pattern).strip().lower()
    exclude = str(exclude).strip().lower()
    include = str(include).strip().lower()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Wordle helper: pattern '{pattern}' (underscore = unknown letter). "
        + (f"Exclude these letters: {exclude}. " if exclude else "")
        + (f"Must include somewhere: {include}. " if include else "")
        +
        "List up to 20 valid 5-letter English words that match. Respond with JSON: {\"words\": [\"word1\", ...]}."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Wordle helper error: {exc}")

    words = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            words = result.get("words") or result.get("suggestions") or []
    except Exception:
        pass
    data = {"pattern": pattern, "exclude": exclude, "include": include, "words": words[:20]}
    return {"status": "ok", "error": None, "data": data}
