import json

from config import OPENAI_MODEL, get_openai_client


SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    sign = (params.get("sign") or params.get("zodiac") or "").strip().lower()
    if not sign or sign not in SIGNS:
        return _error("Missing or invalid sign: use one of " + ", ".join(SIGNS))

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"Write a short, positive daily horoscope (2-4 sentences) for the zodiac sign {sign}. "
        "Tone: encouraging and general. Do not include the sign name in the text. "
        "Respond with a JSON object with keys: \"horoscope\" (the text) and \"date\" (today's date in YYYY-MM-DD)."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Horoscope error: {exc}")

    horoscope_text = ""
    date_str = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            horoscope_text = result.get("horoscope") or result.get("text") or ""
            date_str = result.get("date") or ""
    except Exception:
        pass
    if not horoscope_text:
        horoscope_text = content

    data = {"sign": sign, "horoscope": horoscope_text, "date": date_str}
    return {"status": "ok", "error": None, "data": data}
