import json
import time
import uuid
from urllib.parse import quote


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    chart_type = params.get("type", "bar")
    labels = params.get("labels")
    datasets = params.get("datasets")
    title = params.get("title")
    options = params.get("options")
    width = params.get("width")
    height = params.get("height")
    fmt = params.get("format", "png")

    if not labels or not isinstance(labels, list):
        return _error("Missing required parameter: labels")
    if not datasets or not isinstance(datasets, list):
        return _error("Missing required parameter: datasets")

    chart_config = {
        "type": chart_type,
        "data": {"labels": labels, "datasets": datasets},
        "options": {"plugins": {"title": {"display": bool(title), "text": title}}} if title else {},
    }
    if options and isinstance(options, dict):
        chart_config["options"].update(options)

    query = quote(json.dumps(chart_config, separators=(",", ":")))
    url = f"https://quickchart.io/chart?c={query}"
    if width:
        url += f"&width={int(width)}"
    if height:
        url += f"&height={int(height)}"
    if fmt:
        url += f"&format={fmt}"

    return {
        "status": "ok",
        "error": None,
        "data": {
            "id": str(uuid.uuid4()),
            "format": f".{fmt}",
            "expires": int((time.time() + 86400) * 1000),
            "type": chart_type,
            "downloadURL": url,
        },
    }
