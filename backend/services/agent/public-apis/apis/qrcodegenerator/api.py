import base64
import io
import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    content = params.get("content") or params.get("text") or params.get("data") or params.get("url")
    size = params.get("size") or 200
    if content is None or str(content).strip() == "":
        return _error("Missing required parameter: content or text or data")
    try:
        size = int(size)
        size = max(50, min(500, size))
    except (TypeError, ValueError):
        size = 200
    raw = str(content).strip()
    try:
        url = f"https://api.qrserver.com/v1/create-qr-code/?size={size}x{size}&data={requests.utils.quote(raw)}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        b64 = base64.b64encode(r.content).decode("ascii")
        data = {"content": raw, "image_base64": b64, "size": size}
        return {"status": "ok", "error": None, "data": data}
    except requests.RequestException as e:
        return _error(f"QR code generation failed: {e}")
