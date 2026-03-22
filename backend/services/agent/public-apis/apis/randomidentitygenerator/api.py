import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    gender = (params.get("gender") or "").strip().lower() or None
    country = (params.get("country") or "").strip() or None
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Generate one random fake identity (for testing/placeholder only): full name, gender, birth date (YYYY-MM-DD), street address, city, country, email (realistic format), phone (E.164 style). "
        + (f"Gender: {gender}. " if gender in ("male", "female", "other") else "")
        + (f"Country: {country}. " if country else "")
        + "Respond with a single JSON object with keys: name, gender, birth_date, address, city, country, email, phone. Use only the keys listed."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Identity generator error: {exc}")

    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return {"status": "ok", "error": None, "data": data}
    except Exception:
        pass
    return _error("Could not parse identity response")
