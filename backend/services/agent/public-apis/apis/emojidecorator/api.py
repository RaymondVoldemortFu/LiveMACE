import random


DEFAULT_EMOJIS = ["✨", "🔥", "🎉", "😊", "💡", "🚀", "🌟", "🎯", "💪", "✅"]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    style = (params.get("style") or "wrap").lower()
    density = params.get("density", 0.3)

    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")
    try:
        density = float(density)
    except (TypeError, ValueError):
        return _error("Invalid density: must be a number")

    words = text.split()
    if style == "sprinkle":
        decorated = []
        for word in words:
            decorated.append(word)
            if random.random() < density:
                decorated.append(random.choice(DEFAULT_EMOJIS))
        result = " ".join(decorated)
    else:
        emoji = random.choice(DEFAULT_EMOJIS)
        result = f"{emoji} {text} {emoji}"

    data = {"text": text, "decorated": result, "style": style}
    return {"status": "ok", "error": None, "data": data}
