import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    address = params.get("address") or params.get("text")
    if address is None or not str(address).strip():
        return _error("Missing required parameter: address")
    address = str(address).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Parse this street address into components. Respond with a JSON object with keys: "
        "street_number, street_name, unit, city, state, postal_code, country. "
        "Use empty string for missing parts."
        f"\n\nAddress: {address}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Address parser error: {exc}")

    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            data = {"address": address, "parsed": parsed}
            return {"status": "ok", "error": None, "data": data}
    except Exception:
        pass
    data = {"address": address, "parsed": {}}
    return {"status": "ok", "error": None, "data": data}
