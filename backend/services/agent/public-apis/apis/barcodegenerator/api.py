import hashlib
import urllib.parse


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    data = params.get("data")
    barcode_type = params.get("type")
    if not data or not isinstance(data, str):
        return _error("Missing required parameter: data")
    if not barcode_type or not isinstance(barcode_type, str):
        return _error("Missing required parameter: type")

    bcid = barcode_type.strip().lower()
    if bcid not in {"code128", "code39"}:
        return _error("Invalid type. Allowed: code128, code39")

    query = {
        "bcid": bcid,
        "text": data,
        "scale": "3",
        "includetext": "true" if params.get("displayValue", True) else "false",
    }

    line_color = params.get("lineColor")
    if isinstance(line_color, str) and line_color.strip():
        query["fgcolor"] = line_color.strip().lstrip("#")

    background_color = params.get("backgroundColor")
    if isinstance(background_color, str) and background_color.strip():
        query["bgcolor"] = background_color.strip().lstrip("#")

    base_url = "https://bwipjs-api.metafloor.com/"
    download_url = f"{base_url}?{urllib.parse.urlencode(query)}"

    image_name = hashlib.sha1(download_url.encode("utf-8")).hexdigest() + ".png"

    return {
        "status": "ok",
        "error": None,
        "data": {
            "imageName": image_name,
            "format": ".png",
            "type": bcid.upper(),
            "expires": None,
            "downloadURL": download_url,
        },
    }
