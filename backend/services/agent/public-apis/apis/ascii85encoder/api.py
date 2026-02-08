import base64


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _format_value(fmt: str) -> str:
    fmt = (fmt or "standard").strip().lower()
    if fmt not in {"standard", "btoa"}:
        raise ValueError("Invalid format. Allowed: standard, btoa")
    return fmt


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if not text or not isinstance(text, str):
        return _error("Missing required parameter: text")

    action = (params.get("action") or "encode").strip().lower()
    if action not in {"encode", "decode"}:
        return _error("Invalid action. Allowed: encode, decode")

    try:
        fmt = _format_value(params.get("format", "standard"))
    except ValueError as exc:
        return _error(str(exc))

    try:
        if action == "encode":
            data = text.encode("utf-8")
            encoded = base64.a85encode(data, adobe=(fmt == "btoa")).decode("ascii")
            original_length = len(text)
            encoded_length = len(encoded)
            ratio = f"{encoded_length / max(1, original_length) * 100:.2f}%"
            return {
                "status": "ok",
                "error": None,
                "data": {
                    "original_text": text,
                    "encoded": encoded,
                    "format": fmt,
                    "original_length": original_length,
                    "encoded_length": encoded_length,
                    "compression_ratio": ratio,
                },
            }

        decoded = base64.a85decode(text.encode("ascii"), adobe=(fmt == "btoa")).decode("utf-8", errors="replace")
        decoded_length = len(decoded)
        original_length = len(text)
        ratio = f"{decoded_length / max(1, original_length) * 100:.2f}%"
        return {
            "status": "ok",
            "error": None,
            "data": {
                "original_text": text,
                "encoded": decoded,
                "format": fmt,
                "original_length": original_length,
                "encoded_length": decoded_length,
                "compression_ratio": ratio,
            },
        }
    except (ValueError, UnicodeError) as exc:
        return _error(f"ASCII85 {action} failed: {exc}")
