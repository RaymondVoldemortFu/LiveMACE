import uuid


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    guid = str(uuid.uuid4())
    return {"status": "ok", "error": None, "data": {"guid": guid}}
