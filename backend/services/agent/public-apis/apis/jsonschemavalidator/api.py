import json


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _load_json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def run(params: dict) -> dict:
    params = params or {}
    schema = params.get("schema")
    instance = params.get("data")
    if schema is None or instance is None:
        return _error("Missing required parameters: schema, data")

    try:
        schema = _load_json(schema)
        instance = _load_json(instance)
    except ValueError:
        return _error("Invalid JSON input")

    def _validate(value, sch, path=""):
        errs = []
        sch_type = sch.get("type")
        if sch_type == "object":
            if not isinstance(value, dict):
                return [{"path": path, "message": "Expected object"}]
            required = sch.get("required") or []
            for key in required:
                if key not in value:
                    errs.append({"path": f"{path}/{key}", "message": "Missing required field"})
            props = sch.get("properties") or {}
            for key, sub in props.items():
                if key in value:
                    errs.extend(_validate(value[key], sub, f"{path}/{key}"))
        elif sch_type == "array":
            if not isinstance(value, list):
                return [{"path": path, "message": "Expected array"}]
            item_schema = sch.get("items") or {}
            for idx, item in enumerate(value):
                errs.extend(_validate(item, item_schema, f"{path}/{idx}"))
        elif sch_type == "string":
            if not isinstance(value, str):
                errs.append({"path": path, "message": "Expected string"})
        elif sch_type == "integer":
            if not isinstance(value, int):
                errs.append({"path": path, "message": "Expected integer"})
        elif sch_type == "number":
            if not isinstance(value, (int, float)):
                errs.append({"path": path, "message": "Expected number"})
        elif sch_type == "boolean":
            if not isinstance(value, bool):
                errs.append({"path": path, "message": "Expected boolean"})
        elif sch_type == "null":
            if value is not None:
                errs.append({"path": path, "message": "Expected null"})
        return errs

    errors = _validate(instance, schema, "")
    data = {"valid": not errors, "errors": errors}
    return {"status": "ok", "error": None, "data": data}
