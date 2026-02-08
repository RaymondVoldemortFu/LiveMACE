import random


TONE_WORDS = {
    "positive": ["great", "awesome", "fantastic", "amazing", "brilliant"],
    "neutral": ["interesting", "thoughtful", "noted", "clear", "useful"],
    "negative": ["questionable", "odd", "confusing", "off", "disappointing"],
}

EMOJIS = ["✨", "🔥", "💯", "😍", "👏", "😊", "👍", "🙌"]

TEMPLATES = {
    "text": [
        "This is {tone_word}! {closer}",
        "Really {tone_word} take. {closer}",
        "I think this is {tone_word}. {closer}",
        "What a {tone_word} point! {closer}",
        "So {tone_word} and well said. {closer}",
    ],
    "picture": [
        "Love this shot, so {tone_word}. {closer}",
        "This photo looks {tone_word}! {closer}",
        "Absolutely {tone_word} capture. {closer}",
        "Such a {tone_word} vibe here. {closer}",
        "This is {tone_word}, wow. {closer}",
    ],
}

CLOSERS = ["Wow!", "Nice!", "Well done!", "Love it!", "Great share!"]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    mode = params.get("mode")
    tone = params.get("tone", "positive")
    count = params.get("count", 1)
    emojis = params.get("emojis", True)

    if not mode or not isinstance(mode, str):
        return _error("Missing required parameter: mode")

    mode = mode.lower().strip()
    if mode not in {"text", "picture"}:
        return _error("Invalid mode: must be text or picture")

    tone = str(tone).lower().strip()
    if tone not in TONE_WORDS:
        return _error("Invalid tone: must be positive, negative, or neutral")

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer between 1 and 10")

    if count < 1 or count > 10:
        return _error("Invalid count: must be between 1 and 10")

    emojis = bool(emojis)
    comments = []
    for _ in range(count):
        template = random.choice(TEMPLATES[mode])
        tone_word = random.choice(TONE_WORDS[tone])
        closer = random.choice(CLOSERS)
        comment = template.format(tone_word=tone_word, closer=closer)
        if emojis:
            comment = f"{comment} {random.choice(EMOJIS)}"
        comments.append(comment)

    return {
        "status": "ok",
        "error": None,
        "data": {"count": count, "mode": mode, "tone": tone, "comments": comments},
    }
