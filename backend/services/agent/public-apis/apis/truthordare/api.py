import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    type_ = (params.get("type") or params.get("variant") or "truth").strip().lower()
    if type_ not in ("truth", "dare"):
        type_ = "truth"

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Generate one family-friendly {type_} question or dare for a party game. "
        "One sentence only. Respond with a JSON object with keys: \"text\", \"type\" (truth or dare)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Truth or dare error: {exc}")

    text = type_
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            text = result.get("text") or result.get("prompt") or content
            type_ = result.get("type") or type_
    except Exception:
        text = content

    data = {"text": text, "type": type_}
    return {"status": "ok", "error": None, "data": data}
