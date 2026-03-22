import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    category = (params.get("category") or "").strip() or None
    difficulty = (params.get("difficulty") or "").strip() or None
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Generate one trivia question with 4 multiple-choice answers (A, B, C, D) and indicate the correct letter. "
        + (f"Category: {category}. " if category else "")
        + (f"Difficulty: {difficulty}. " if difficulty else "Mixed difficulty. ")
        +
        'Respond with a JSON object with keys: "question", "options" (object with keys A, B, C, D), "answer" (letter A/B/C/D).'
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Trivia error: {exc}")

    question = options = answer = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            question = result.get("question") or ""
            options = result.get("options") or {}
            answer = result.get("answer") or ""
    except Exception:
        pass

    data = {"question": question, "options": options, "answer": answer, "category": category, "difficulty": difficulty}
    return {"status": "ok", "error": None, "data": data}
