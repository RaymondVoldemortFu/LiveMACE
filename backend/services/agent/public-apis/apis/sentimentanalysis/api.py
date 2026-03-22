import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Classify the sentiment of the following text as one of: positive, negative, neutral. "
        "Also give a confidence score between 0 and 1. "
        "Respond with a JSON object with keys: \"sentiment\", \"confidence\"."
        f"\n\nText: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Sentiment analysis error: {exc}")

    sentiment = "neutral"
    confidence = 0.5
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            sentiment = (result.get("sentiment") or "neutral").lower()
            if sentiment not in ("positive", "negative", "neutral"):
                sentiment = "neutral"
            confidence = float(result.get("confidence") or 0.5)
            confidence = max(0, min(1, confidence))
    except Exception:
        pass

    data = {"text": text, "sentiment": sentiment, "confidence": confidence}
    return {"status": "ok", "error": None, "data": data}
