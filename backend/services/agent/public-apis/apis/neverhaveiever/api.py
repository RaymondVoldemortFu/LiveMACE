import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Generate exactly one new \"Never have I ever\" statement for a party game. "
        "It must be a short, fun, family-friendly phrase that completes the sentence \"Never have I ever ___.\" "
        "Do not repeat common clichés; be creative and varied. "
        "Respond with a JSON object with a single key \"phrase\" whose value is only the completion (no period, no \"Never have I ever\" prefix). "
        "Example: {\"phrase\": \"tried bungee jumping\"}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Never have I ever error: {exc}")

    phrase = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            phrase = (result.get("phrase") or result.get("prompt") or "").strip()
    except Exception:
        pass
    if not phrase:
        phrase = content.strip().strip('"').strip()

    data = {"prompt": f"Never have I ever {phrase}."}
    return {"status": "ok", "error": None, "data": data}
