import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    swift = params.get("swift") or params.get("bic") or params.get("code")
    if not swift or not str(swift).strip():
        return _error("Missing required parameter: swift or bic")
    code = str(swift).strip().upper().replace(" ", "")
    if len(code) < 8:
        return _error("SWIFT/BIC should be 8 or 11 characters")
    try:
        r = requests.get(
            "https://openapi.iban.com/clients/api/swift/v1/" + code,
            timeout=15,
        )
        if r.status_code != 200:
            data = {"swift": code, "bank": None, "country": None, "note": "SWIFT lookup service unavailable"}
            return {"status": "ok", "error": None, "data": data}
        j = r.json()
        data = {"swift": code, "bank": j.get("bank"), "country": j.get("country"), "city": j.get("city")}
        return {"status": "ok", "error": None, "data": data}
    except requests.RequestException as e:
        return _error(str(e))
