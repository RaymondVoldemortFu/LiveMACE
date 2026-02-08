import json

import requests

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
    url = params.get("url")
    limit = params.get("limit", 5)

    if not url or not isinstance(url, str):
        return _error("Missing required parameter: url")

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return _error("Invalid limit: must be an integer")

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        content = response.text
    except requests.RequestException as exc:
        return _error(f"Failed to fetch URL: {exc}")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    snippet = content[:8000]
    prompt = (
        "Extract contact information from the following webpage content. "
        "Return ONLY JSON with keys: emails (array), phones (array), places (array). "
        "Limit each array to at most the provided limit if limit > 0. "
        f"Limit: {limit}\n"
        "Content:\n"
        f"{snippet}"
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
        return _error(f"Contact extractor error: {exc}")

    if not isinstance(payload, dict):
        return _error("Contact extractor returned invalid response")

    emails = payload.get("emails") or []
    phones = payload.get("phones") or []
    places = payload.get("places") or []

    if limit > 0:
        emails = emails[:limit]
        phones = phones[:limit]
        places = places[:limit]

    return {
        "status": "ok",
        "error": None,
        "data": {"url": url, "emails": emails, "phones": phones, "places": places},
    }
