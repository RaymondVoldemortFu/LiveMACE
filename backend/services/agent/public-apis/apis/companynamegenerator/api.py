import random


PREFIXES = ["Neo", "Bright", "True", "Sky", "Prime", "Core", "Vision", "United", "Next"]
SUFFIXES = ["Labs", "Systems", "Works", "Dynamics", "Tech", "Solutions", "Group", "Studio", "Cloud"]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _title(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def run(params: dict) -> dict:
    params = params or {}
    keyword = params.get("keyword")
    count = params.get("count", 5)

    if not keyword or not isinstance(keyword, str):
        return _error("Missing required parameter: keyword")

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer between 1 and 10")

    if count < 1 or count > 10:
        return _error("Invalid count: must be between 1 and 10")

    keyword = _title(keyword.strip())
    names = []
    for _ in range(count):
        pattern = random.choice(["prefix", "suffix", "both"])
        if pattern == "prefix":
            name = f"{random.choice(PREFIXES)}{keyword}"
        elif pattern == "suffix":
            name = f"{keyword}{random.choice(SUFFIXES)}"
        else:
            name = f"{random.choice(PREFIXES)}{keyword}{random.choice(SUFFIXES)}"
        names.append(name)

    return {
        "status": "ok",
        "error": None,
        "data": {"keyword": keyword.lower(), "count": str(count), "names": names},
    }
