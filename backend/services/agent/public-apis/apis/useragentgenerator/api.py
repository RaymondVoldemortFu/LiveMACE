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
        "Generate exactly one realistic, current User-Agent string for a web browser (Chrome, Firefox, Safari, or Edge) on Windows, Mac, or Linux. "
        "Output only a JSON object with key \"user_agent\" whose value is the full User-Agent string. No other text."
    )
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"User-Agent generator error: {exc}")
    ua = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            ua = result.get("user_agent") or result.get("ua") or ""
    except Exception:
        ua = content.strip().strip('"')
    if not ua:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    data = {"user_agent": ua}
    return {"status": "ok", "error": None, "data": data}
