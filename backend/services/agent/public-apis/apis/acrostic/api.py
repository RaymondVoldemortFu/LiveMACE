import html
import json

from config import OPENAI_MODEL, get_openai_client


ALLOWED_THEMES = {"random", "positive", "nature", "adventure", "friendship", "learning"}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _build_html(keyword: str, lines: list) -> str:
    safe_keyword = html.escape(keyword)
    parts = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'><title>Acrostic Puzzle</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;padding:20px;max-width:700px;margin:0 auto;}",
        "h1{text-align:center;color:#FF5722;}",
        ".intro{text-align:center;color:#666;margin-bottom:30px;}",
        ".line{display:flex;align-items:center;margin:10px 0;padding:10px;background:#f5f5f5;border-radius:5px;}",
        ".number{width:30px;font-weight:bold;color:#FF5722;}",
        ".first-letter{width:40px;height:40px;background:#FF5722;color:white;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:bold;border-radius:5px;margin-right:10px;}",
        ".blanks{display:flex;gap:3px;margin-right:15px;}",
        ".blank{width:25px;height:30px;border-bottom:2px solid #333;}",
        ".clue{flex:1;font-size:14px;color:#666;font-style:italic;}",
        ".keyword{text-align:center;margin-top:30px;padding:20px;background:#FFF3E0;border-radius:10px;}",
        ".keyword-letters{display:flex;justify-content:center;gap:10px;}",
        ".keyword-letter{width:40px;height:40px;background:#FF5722;color:white;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:bold;border-radius:5px;}",
        "</style></head><body>",
        "<h1>Acrostic Puzzle</h1>",
        "<div class='intro'>Solve the clues. The first letters spell a word!</div>",
    ]
    for line in lines:
        letter = html.escape(line.get("letter", ""))
        clue = html.escape(line.get("clue", ""))
        blanks = "".join("<span class='blank'></span>" for _ in range(max(1, line.get("letterCount", 0) - 1)))
        parts.append(
            "<div class='line'>"
            f"<span class='number'>{line.get('position')}.</span>"
            f"<span class='first-letter'>{letter}</span>"
            f"<span class='blanks'>{blanks}</span>"
            f"<span class='clue'>{clue}</span>"
            "</div>"
        )
    parts.append("<div class='keyword'><p>Hidden word:</p><div class='keyword-letters'>")
    parts.extend("<div class='keyword-letter'>?</div>" for _ in safe_keyword)
    parts.append("</div></div></body></html>")
    return "".join(parts)


def run(params: dict) -> dict:
    word = (params or {}).get("word")
    theme = (params or {}).get("theme", "random")
    if not word or not isinstance(word, str):
        return _error("Missing required parameter: word")
    keyword = word.strip()
    if len(keyword) < 3 or len(keyword) > 15:
        return _error("Parameter word must be 3-15 letters")

    if not theme or not isinstance(theme, str):
        theme = "random"
    theme = theme.strip().lower() or "random"
    if theme not in ALLOWED_THEMES:
        return _error("Invalid theme. Allowed: random, positive, nature, adventure, friendship, learning")

    client = get_openai_client()
    prompt = (
        "Create an acrostic puzzle for the keyword. "
        "Return JSON with keys: keyword (string), theme (string), lines (array). "
        "Each line item: position (number), letter (string), answer (string), "
        "letterCount (number), clue (string). "
        "Answers must start with the specified letter and be valid English words. "
        "Provide concise dictionary-style clues. Do not include extra keys."
    )
    user = f"Keyword: {keyword}\nTheme: {theme}"

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    lines = data.get("lines") or []
    if len(lines) != len(keyword):
        return _error("Failed to generate a full acrostic puzzle")

    html_body = _build_html(keyword, lines)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "keyword": keyword,
            "theme": theme,
            "lines": lines,
            "lineCount": len(lines),
            "html": html_body,
            "image": None,
        },
    }
