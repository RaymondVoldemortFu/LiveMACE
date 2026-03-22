import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("words")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Convert the number written in words to digits: \"{text}\". "
        "Respond with a JSON object with key \"number\" (integer or float)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Words to numbers error: {exc}")

    number = None
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            number = result.get("number")
    except Exception:
        pass
    if number is not None:
        try:
            number = float(number) if "." in str(number) else int(float(number))
        except (TypeError, ValueError):
            number = None
    data = {"text": text, "number": number}
    return {"status": "ok", "error": None, "data": data}
