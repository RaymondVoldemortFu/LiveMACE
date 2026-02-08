import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    style = (params.get("style") or params.get("type") or "lorem").strip().lower()
    length = params.get("length") or params.get("sentences") or 3
    try:
        length = int(length)
        length = max(1, min(20, length))
    except (TypeError, ValueError):
        length = 3

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    style_desc = {
        "lorem": "classic lorem ipsum style",
        "tech": "tech jargon and software terms",
        "corporate": "corporate buzzwords",
        "hipster": "hipster and artisanal",
        "pirate": "pirate slang",
        "fantasy": "fantasy and medieval",
        "scifi": "sci-fi and futuristic",
    }.get(style, style or "neutral placeholder")

    prompt = (
        f"Generate exactly {length} short sentences of placeholder text in this style: {style_desc}. "
        "Output only the paragraph(s), no labels or quotes. One line or a short block is fine."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Placeholder text error: {exc}")

    data = {"style": style, "text": text, "sentences": length}
    return {"status": "ok", "error": None, "data": data}
