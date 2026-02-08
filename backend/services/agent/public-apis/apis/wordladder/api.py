import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    start = params.get("start") or params.get("from")
    end = params.get("end") or params.get("to")
    if not start or not end:
        return _error("Missing required parameters: start, end")
    start = str(start).strip().lower()
    end = str(end).strip().lower()
    if len(start) != len(end):
        return _error("Start and end words must be the same length")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Find a word ladder from '{start}' to '{end}' (each step change one letter, must be a real English word). "
        "Respond with a JSON object with key \"ladder\" (array of words from start to end). Use as few steps as possible."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Word ladder error: {exc}")

    ladder = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            ladder = result.get("ladder") or result.get("path") or []
    except Exception:
        pass
    data = {"start": start, "end": end, "ladder": ladder}
    return {"status": "ok", "error": None, "data": data}
