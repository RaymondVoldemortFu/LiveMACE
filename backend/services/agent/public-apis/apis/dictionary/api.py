import requests


DICT_URL = "https://api.dictionaryapi.dev/api/v2/entries/en"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    word = params.get("word")

    if not word or not isinstance(word, str):
        return _error("Missing required parameter: word")

    try:
        response = requests.get(f"{DICT_URL}/{word}", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Dictionary service error: {exc}")
    except ValueError:
        return _error("Dictionary service returned invalid JSON")

    if not isinstance(payload, list) or not payload:
        return _error("Dictionary service returned empty response")

    definitions = []
    for entry in payload:
        for meaning in entry.get("meanings", []) or []:
            for definition in meaning.get("definitions", []) or []:
                text = definition.get("definition")
                if text:
                    definitions.append(text)

    data = {
        "word": word,
        "definitionCount": len(definitions),
        "definitions": definitions,
    }
    return {"status": "ok", "error": None, "data": data}
