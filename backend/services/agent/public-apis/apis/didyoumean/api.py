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
    query = params.get("query")

    if not query or not isinstance(query, str):
        return _error("Missing required parameter: query")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "You are a spelling correction assistant. Suggest up to 5 corrected phrases "
        "for the input query. Return ONLY JSON with keys: query, didYouMean (array). "
        f"Query: {query}"
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
        return _error(f"Did you mean error: {exc}")

    did_you_mean = payload.get("didYouMean")
    if not isinstance(payload, dict) or not isinstance(did_you_mean, list):
        return _error("Did you mean returned invalid response")

    data = {"query": query, "didYouMean": did_you_mean}
    return {"status": "ok", "error": None, "data": data}
