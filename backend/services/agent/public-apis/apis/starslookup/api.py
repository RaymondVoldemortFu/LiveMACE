import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    name = params.get("name") or params.get("star") or params.get("query")
    if not name or not str(name).strip():
        return _error("Missing required parameter: name or query")
    query = str(name).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Give brief astronomical info for the star (or object) named '{query}': "
        "constellation, approximate magnitude, distance if commonly known, and one sentence description. "
        "Respond with a JSON object with keys: name, constellation, magnitude, distance_ly (number or null), description."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Stars lookup error: {exc}")

    result = {}
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if not isinstance(result, dict):
            result = {"description": content}
    except Exception:
        result = {"description": content}

    data = {"query": query, "result": result}
    return {"status": "ok", "error": None, "data": data}
