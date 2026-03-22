import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    count = params.get("count") or params.get("n") or 1
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
        f"Generate {count} fake user profile(s) for testing. Each has: username (one word, maybe numbers), first name, last name, email, and a short bio (one sentence). "
        "Respond with a JSON object with key \"users\" being an array of objects, each with keys: username, first_name, last_name, email, bio."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Random user generator error: {exc}")

    users = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            users = result.get("users") or result.get("results") or []
        if not isinstance(users, list):
            users = [result] if isinstance(result, dict) else []
    except Exception:
        pass

    data = {"users": users[:count], "count": len(users[:count])}
    return {"status": "ok", "error": None, "data": data}
