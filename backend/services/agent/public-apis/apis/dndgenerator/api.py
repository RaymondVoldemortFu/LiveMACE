import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
    raise ValueError("Invalid JSON")


def run(params: dict) -> dict:
    params = params or {}
    content_type = str(params.get("type", "all")).lower()
    count = params.get("count", 1)

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer")
    if count < 1 or count > 10:
        return _error("Invalid count: must be between 1 and 10")

    allowed = {"all", "character", "npc", "monster", "treasure", "encounter", "tavern", "quest", "name"}
    if content_type not in allowed:
        return _error("Invalid type: unsupported content type")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Generate random D&D content. Return ONLY JSON with keys: type, count, results. "
        "If count is 1, results should be an object; otherwise results should be an array. "
        f"Type: {content_type}. Count: {count}."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        content = response.choices[0].message.content or ""
        payload = _extract_json(content)
    except Exception as exc:
        return _error(f"D&D generator error: {exc}")

    if not isinstance(payload, dict) or "results" not in payload:
        return _error("D&D generator returned invalid response")

    payload["type"] = content_type
    payload["count"] = count
    return {"status": "ok", "error": None, "data": payload}
