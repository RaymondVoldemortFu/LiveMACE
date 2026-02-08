import hmac
import hashlib
import struct
import time
import base64


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _hotp(secret: bytes, counter: int, digits: int = 6) -> str:
    msg = struct.pack(">Q", counter)
    h = hmac.new(secret, msg, hashlib.sha1).digest()
    offset = h[19] & 0xF
    code = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def run(params: dict) -> dict:
    params = params or {}
    action = (params.get("action") or "").strip().lower()
    secret = params.get("secret")
    if action == "generate" or (not secret and action != "verify"):
        import secrets
        secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
        data = {"secret": secret, "uri": f"otpauth://totp/Label?secret={secret}"}
        return {"status": "ok", "error": None, "data": data}
    if not secret:
        return _error("Missing required parameter: secret to get TOTP code")
    try:
        secret = base64.b32decode(secret.upper() + "=" * (8 - len(secret) % 8))
    except Exception:
        return _error("Invalid base32 secret")
    counter = int(time.time()) // 30
    code = _hotp(secret, counter)
    data = {"secret": "***", "code": code, "valid_for_seconds": 30 - int(time.time()) % 30}
    return {"status": "ok", "error": None, "data": data}
