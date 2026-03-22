import mimetypes


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    extension = params.get("extension")
    mime = params.get("mime")

    if not extension and not mime:
        return _error("Missing required parameter: extension or mime")

    if extension:
        ext = extension if extension.startswith(".") else f".{extension}"
        mime_type = mimetypes.types_map.get(ext.lower())
        data = {"extension": extension, "mime": mime_type}
        return {"status": "ok", "error": None, "data": data}

    exts = [k for k, v in mimetypes.types_map.items() if v == mime]
    data = {"mime": mime, "extensions": exts}
    return {"status": "ok", "error": None, "data": data}
