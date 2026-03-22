import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    routing = params.get("routing") or params.get("routing_number") or params.get("aba")
    if routing is None or str(routing).strip() == "":
        return _error("Missing required parameter: routing or routing_number")
    s = str(routing).strip()
    if not s.isdigit() or len(s) != 9:
        return _error("US routing number must be 9 digits")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"What bank or institution is US ABA routing number {s} associated with? "
        "Respond with a JSON object with keys: \"bank_name\", \"location\" (city/state if known), \"valid\" (true/false). "
        "If unknown, set bank_name to \"Unknown\" and valid to true (format can still be valid)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Routing lookup error: {exc}")

    bank_name = location = "Unknown"
    valid = True
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            bank_name = result.get("bank_name") or result.get("name") or "Unknown"
            location = result.get("location") or ""
            valid = result.get("valid", True)
    except Exception:
        pass

    data = {"routing_number": s, "bank_name": bank_name, "location": location, "valid": valid}
    return {"status": "ok", "error": None, "data": data}
