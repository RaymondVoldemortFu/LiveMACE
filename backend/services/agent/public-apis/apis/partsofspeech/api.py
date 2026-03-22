import json
import re

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
        "For each word in the following text, output its part of speech. "
        "Use standard POS tags: noun, verb, adjective, adverb, pronoun, preposition, conjunction, interjection, determiner, number, other. "
        "Respond with a single JSON object with key \"words_by_pos\" mapping each POS to a list of words from the text that have that POS. "
        "Include every word that looks like a word (letters and possibly apostrophes). "
        "Example: {\"words_by_pos\": {\"noun\": [\"dog\"], \"verb\": [\"runs\"]}}. "
        f"Text: {text}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Parts of speech error: {exc}")

    words = re.findall(r"[A-Za-z']+", text)
    by_pos = {}
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            by_pos = result.get("words_by_pos") or result
            if not isinstance(by_pos, dict):
                by_pos = {}
    except Exception:
        pass

    data = {"text": text, "words_by_pos": by_pos, "words": words}
    return {"status": "ok", "error": None, "data": data}
