import json


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _infer_schema(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        items = [_infer_schema(v) for v in value]
        return {"type": "array", "items": items[0]}
    if isinstance(value, dict):
        props = {k: _infer_schema(v) for k, v in value.items()}
        return {"type": "object", "properties": props, "required": list(value.keys())}
    return {"type": "string"}


def run(params: dict) -> dict:
    params = params or {}
    sample = params.get("sample")
    if sample is None:
        return _error("Missing required parameter: sample")

    if isinstance(sample, str):
        try:
            sample = json.loads(sample)
        except ValueError:
            return _error("Invalid sample JSON")

    schema = _infer_schema(sample)
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    data = {"schema": schema}
    return {"status": "ok", "error": None, "data": data}
