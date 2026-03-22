import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word") or params.get("term")
    if word is None or not str(word).strip():
        return _error("Missing required parameter: word")
    word = str(word).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Give synonyms and antonyms for the word \"{word}\". "
        "Respond with a JSON object with keys: \"synonyms\" (array of strings), \"antonyms\" (array of strings)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Thesaurus error: {exc}")

    synonyms = antonyms = []
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            synonyms = result.get("synonyms") or []
            antonyms = result.get("antonyms") or []
            if not isinstance(synonyms, list):
                synonyms = [synonyms]
            if not isinstance(antonyms, list):
                antonyms = [antonyms]
    except Exception:
        pass

    data = {"word": word, "synonyms": synonyms, "antonyms": antonyms}
    return {"status": "ok", "error": None, "data": data}
