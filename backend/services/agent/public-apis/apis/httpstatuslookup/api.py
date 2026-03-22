from http import HTTPStatus


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    code = params.get("code")
    if code is None:
        return _error("Missing required parameter: code")

    try:
        code = int(code)
        status = HTTPStatus(code)
    except (ValueError, KeyError):
        return _error("Unknown HTTP status code")

    data = {"code": code, "name": status.name, "description": status.phrase}
    return {"status": "ok", "error": None, "data": data}
