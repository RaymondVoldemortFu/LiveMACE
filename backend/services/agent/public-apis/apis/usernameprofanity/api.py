import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    username = params.get("username") or params.get("name")
    if username is None or not str(username).strip():
        return _error("Missing required parameter: username")
    username = str(username).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Is the username \"{username}\" inappropriate, offensive, or contain profanity? "
        "Respond with a JSON object with keys: \"inappropriate\" (boolean), \"reason\" (one short sentence if yes)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Username profanity check error: {exc}")

    inappropriate = False
    reason = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            inappropriate = bool(result.get("inappropriate"))
            reason = str(result.get("reason") or "")
    except Exception:
        pass

    data = {"username": username, "inappropriate": inappropriate, "reason": reason}
    return {"status": "ok", "error": None, "data": data}
