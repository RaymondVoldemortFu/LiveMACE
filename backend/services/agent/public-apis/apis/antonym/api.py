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
    word = params.get("word")
    count = params.get("count", 5)
    if not word or not isinstance(word, str):
        return _error("Missing required parameter: word")

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer")
    if count < 1 or count > 20:
        return _error("Invalid count: must be between 1 and 20")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Provide antonyms for the given word. Return ONLY JSON with keys: "
        "word (string), antonyms (array). Limit the array length to the requested count. "
        f"Word: {word}. Count: {count}."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        payload = _extract_json(content)
    except Exception as exc:
        return _error(f"Antonym error: {exc}")

    antonyms = payload.get("antonyms")
    if not isinstance(payload, dict) or not isinstance(antonyms, list):
        return _error("Antonym returned invalid response")

    data = {"word": word, "antonyms": antonyms[:count]}
    return {"status": "ok", "error": None, "data": data}
