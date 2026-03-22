import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))
    prompt = (
        "Invent one short, catchy superhero name (1-3 words) and a one-line superpower. "
        "Respond with a JSON object with keys: \"name\", \"power\"."
    )
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Superhero name error: {exc}")
    name = power = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            name = result.get("name") or ""
            power = result.get("power") or ""
    except Exception:
        pass
    data = {"name": name or "Unknown", "power": power}
    return {"status": "ok", "error": None, "data": data}
