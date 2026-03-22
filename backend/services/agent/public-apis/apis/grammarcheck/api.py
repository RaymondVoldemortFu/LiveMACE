import requests


LANGUAGETOOL_URL = "https://api.languagetool.org/v2/check"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    language = params.get("language", "en-US")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    try:
        response = requests.post(
            LANGUAGETOOL_URL,
            data={"text": text, "language": language},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Grammar check error: {exc}")
    except ValueError:
        return _error("Grammar check service returned invalid JSON")

    matches = payload.get("matches") or []
    data = {
        "language": language,
        "matches": [
            {
                "message": m.get("message"),
                "shortMessage": m.get("shortMessage"),
                "offset": m.get("offset"),
                "length": m.get("length"),
                "replacements": [r.get("value") for r in m.get("replacements") or []],
                "rule": m.get("rule", {}).get("id"),
            }
            for m in matches
        ],
    }
    return {"status": "ok", "error": None, "data": data}
