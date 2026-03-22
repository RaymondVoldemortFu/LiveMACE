import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count") or 1
    style = (params.get("style") or "").strip().lower() or None
    try:
        count = int(count)
        count = max(1, min(20, count))
    except (TypeError, ValueError):
        count = 1

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Generate {count} unique, available-style usernames (lowercase, letters and numbers only, no spaces). "
        + (f"Style: {style}. " if style else "Mix of creative and readable. ")
        +
        'Respond with a JSON object with key "usernames" (array of strings).'
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Username generator error: {exc}")

    usernames = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            usernames = result.get("usernames") or []
    except Exception:
        pass
    if not isinstance(usernames, list):
        usernames = []
    data = {"usernames": usernames[:count], "count": len(usernames[:count])}
    return {"status": "ok", "error": None, "data": data}
