import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


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
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    acronyms = data.get("acronyms") or []
    if len(acronyms) < 3:
        return _error("Failed to generate acronyms")

    return {
        "status": "ok",
        "error": None,
        "data": {"text": text, "acronyms": acronyms[:3]},
    }
