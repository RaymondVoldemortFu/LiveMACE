import random


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


TEMPLATES = [
    "In the near future, you will find clarity about {topic}.",
    "A small change around {topic} will unlock a big opportunity.",
    "Be patient with {topic}; progress is closer than it feels.",
    "Trust your instincts about {topic} and take a bold step.",
    "An unexpected message will shift your outlook on {topic}.",
]


def run(params: dict) -> dict:
    params = params or {}
    topic = params.get("topic") or "your plans"
    if not isinstance(topic, str):
        return _error("Invalid topic: must be a string")

    fortune = random.choice(TEMPLATES).format(topic=topic)
    data = {"topic": topic, "fortune": fortune}
    return {"status": "ok", "error": None, "data": data}
