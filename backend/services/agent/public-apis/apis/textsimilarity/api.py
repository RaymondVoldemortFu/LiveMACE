import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text1 = params.get("text1") or params.get("text_a") or params.get("a")
    text2 = params.get("text2") or params.get("text_b") or params.get("b")
    if text1 is None or text2 is None:
        return _error("Missing required parameters: text1, text2")
    t1 = str(text1).strip()
    t2 = str(text2).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Rate how similar these two texts are in meaning from 0 (completely different) to 1 (identical meaning). "
        "Respond with a JSON object with key \"similarity\" (number 0-1) and \"explanation\" (one short sentence)."
        f"\n\nText 1: {t1}\n\nText 2: {t2}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Similarity error: {exc}")

    similarity = 0.5
    explanation = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            similarity = float(result.get("similarity") or 0.5)
            similarity = max(0, min(1, similarity))
            explanation = str(result.get("explanation") or "")
    except Exception:
        pass

    data = {"text1": t1[:200], "text2": t2[:200], "similarity": similarity, "explanation": explanation}
    return {"status": "ok", "error": None, "data": data}
