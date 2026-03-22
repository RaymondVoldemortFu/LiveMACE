import hashlib


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


ALGORITHMS = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha512": hashlib.sha512,
}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    algo = (params.get("algorithm") or "sha256").lower()

    if text is None:
        return _error("Missing required parameter: text")
    if algo not in ALGORITHMS:
        return _error("Unsupported algorithm")

    digest = ALGORITHMS[algo](str(text).encode("utf-8")).hexdigest()
    data = {"algorithm": algo, "hash": digest}
    return {"status": "ok", "error": None, "data": data}
