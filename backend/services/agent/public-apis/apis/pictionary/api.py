import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    difficulty = (params.get("difficulty") or "medium").strip().lower()
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Generate a single word or short phrase (2-4 words max) that is good for a Pictionary drawing game. "
        f"Difficulty: {difficulty}. "
        "Easy = everyday objects or animals. Medium = actions or concepts. Hard = abstract or compound concepts. "
        "Respond with a JSON object with keys: \"word\" (the prompt to draw) and \"difficulty\" (easy/medium/hard)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Pictionary error: {exc}")

    word = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            word = result.get("word") or result.get("phrase") or ""
    except Exception:
        pass
    if not word:
        word = content.strip().strip('"')

    data = {"word": word, "difficulty": difficulty}
    return {"status": "ok", "error": None, "data": data}
