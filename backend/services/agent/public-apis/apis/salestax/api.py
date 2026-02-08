import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    amount = params.get("amount") or params.get("price")
    location = params.get("location") or params.get("state") or params.get("region") or ""
    if amount is None:
        return _error("Missing required parameter: amount")
    try:
        amount = float(amount)
        if amount < 0:
            return _error("Amount must be non-negative")
    except (TypeError, ValueError):
        return _error("Invalid amount")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    loc_desc = location if location else "United States (general)"
    prompt = (
        f"Estimate sales tax rate (as a percentage, 0-15) for: {loc_desc}. "
        "Respond with a JSON object with keys: \"rate_percent\" (number), \"location\" (short label), \"note\" (one short sentence)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Sales tax lookup error: {exc}")

    rate = 0.0
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            rate = float(result.get("rate_percent") or result.get("rate") or 0)
    except Exception:
        pass
    rate = max(0, min(25, rate))
    tax = round(amount * rate / 100, 2)
    data = {"amount": amount, "rate_percent": rate, "tax_amount": tax, "total": round(amount + tax, 2), "location": location or "US"}
    return {"status": "ok", "error": None, "data": data}
