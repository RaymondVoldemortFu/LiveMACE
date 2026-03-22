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
    code = params.get("code")

    if not code or not isinstance(code, str):
        return _error("Missing required parameter: code")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Detect the programming language of the following code. "
        "Return ONLY JSON with keys: likelihood (0-1), family (uppercase), "
        "current (lowercase language id), readable (human name), extension (with leading dot). "
        "Code:\n"
        f"{code}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content or ""
        payload = _extract_json(content)
    except Exception as exc:
        return _error(f"Code detector error: {exc}")

    required = {"likelihood", "family", "current", "readable", "extension"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        return _error("Code detector returned invalid response")

    return {"status": "ok", "error": None, "data": payload}
