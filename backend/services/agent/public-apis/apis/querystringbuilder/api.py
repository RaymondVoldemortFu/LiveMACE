from urllib.parse import urlencode, parse_qs


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    obj = params.get("object") or params.get("params") or params.get("query")
    mode = (params.get("mode") or "build").strip().lower()
    if obj is None and mode != "parse":
        return _error("Missing required parameter: object or params")
    if mode == "parse":
        qs = params.get("querystring") or params.get("query_string") or ""
        if not str(qs).strip():
            return _error("Missing querystring for parse mode")
        try:
            parsed = parse_qs(str(qs).strip())
            data = {"querystring": str(qs).strip(), "parsed": parsed}
        except Exception as e:
            return _error(str(e))
    else:
        if isinstance(obj, str):
            import json
            try:
                obj = json.loads(obj)
            except Exception:
                return _error("object must be a JSON object or dict")
        if not isinstance(obj, dict):
            return _error("object must be a dict or JSON object")
        flat = {}
        for k, v in obj.items():
            if v is None:
                continue
            flat[k] = [v] if not isinstance(v, (list, tuple)) else list(v)
        querystring = urlencode(flat, doseq=True)
        data = {"object": obj, "querystring": querystring}
    return {"status": "ok", "error": None, "data": data}
