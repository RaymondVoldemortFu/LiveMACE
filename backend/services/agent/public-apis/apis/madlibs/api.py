import random
import re


PLACEHOLDER_RE = re.compile(r"\{(noun|verb|adjective|adverb)\}")

WORDS = {
    "noun": ["dragon", "forest", "robot", "ocean", "mountain"],
    "verb": ["dance", "explore", "build", "whisper", "race"],
    "adjective": ["mysterious", "brave", "ancient", "gleaming", "swift"],
    "adverb": ["quietly", "boldly", "swiftly", "happily", "sadly"],
}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    template = params.get("template")
    if not template or not isinstance(template, str):
        return _error("Missing required parameter: template")

    def _replace(match):
        key = match.group(1)
        return random.choice(WORDS[key])

    result = PLACEHOLDER_RE.sub(_replace, template)
    data = {"template": template, "result": result}
    return {"status": "ok", "error": None, "data": data}
