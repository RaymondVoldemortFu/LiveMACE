import hmac
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
    message = params.get("message")
    key = params.get("key")
    algo = (params.get("algorithm") or "sha256").lower()

    if message is None or key is None:
        return _error("Missing required parameters: message, key")
    if algo not in ALGORITHMS:
        return _error("Unsupported algorithm")

    digest = hmac.new(str(key).encode("utf-8"), str(message).encode("utf-8"), ALGORITHMS[algo]).hexdigest()
    data = {"algorithm": algo, "hmac": digest}
    return {"status": "ok", "error": None, "data": data}
