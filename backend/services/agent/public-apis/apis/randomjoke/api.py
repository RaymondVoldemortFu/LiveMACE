import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    category = (params.get("category") or "").strip().lower() or None
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Tell one short, family-friendly joke (setup and punchline, or one-liner). "
        + (f"Category or theme: {category}. " if category else "")
        + "Respond with a JSON object with keys: \"joke\" (full joke text) and \"category\" (short label)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Random joke error: {exc}")

    joke = category_out = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            joke = result.get("joke") or result.get("text") or ""
            category_out = result.get("category") or ""
    except Exception:
        pass
    if not joke:
        joke = content

    data = {"joke": joke, "category": category_out or category}
    return {"status": "ok", "error": None, "data": data}
