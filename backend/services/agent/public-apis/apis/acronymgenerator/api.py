import json
import re

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_json_object(text: str) -> dict:
    if not text:
        return {}
    candidate = text.strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def run(params: dict) -> dict:
    text = (params or {}).get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")
    text = text.strip()
    if not text:
        return _error("Missing required parameter: text")
    if len(text) > 200:
        return _error("Parameter text exceeds 200 characters")

    client = get_openai_client()
    prompt = (
        "Generate 3 unique acronyms from the given phrase. "
        "Return JSON with keys: text (string), acronyms (array of 3 strings). "
        "Do not include extra keys."
    )
    user = f"Text: {text}"

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
    )
    content = response.choices[0].message.content or "{}"
    data = _parse_json_object(content)
    acronyms = data.get("acronyms") or []
    if len(acronyms) < 3:
        return _error("Failed to generate acronyms")

    return {
        "status": "ok",
        "error": None,
        "data": {"text": text, "acronyms": acronyms[:3]},
    }
