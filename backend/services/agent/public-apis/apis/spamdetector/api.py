import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text") or params.get("content")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Is the following message likely spam (e.g. promotional, scam, unsolicited)? "
        "Respond with a JSON object with keys: \"is_spam\" (boolean), \"confidence\" (0-1), \"reason\" (one short sentence)."
        f"\n\nMessage: {text[:2000]}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Spam detector error: {exc}")

    is_spam = False
    confidence = 0.5
    reason = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            is_spam = bool(result.get("is_spam"))
            confidence = float(result.get("confidence") or 0.5)
            confidence = max(0, min(1, confidence))
            reason = str(result.get("reason") or "")
    except Exception:
        pass

    data = {"text": text[:500], "is_spam": is_spam, "confidence": confidence, "reason": reason}
    return {"status": "ok", "error": None, "data": data}
