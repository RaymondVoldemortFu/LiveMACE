def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _format_row(values):
    return "| " + " | ".join(str(v) for v in values) + " |"


def run(params: dict) -> dict:
    params = params or {}
    headers = params.get("headers")
    rows = params.get("rows")
    align = params.get("align") or []

    if not headers or not isinstance(headers, list):
        return _error("Missing required parameter: headers")
    if rows is None or not isinstance(rows, list):
        return _error("Missing required parameter: rows")

    align_map = {"left": ":---", "center": ":---:", "right": "---:"}
    align_row = []
    for idx, _ in enumerate(headers):
        if idx < len(align) and align[idx] in align_map:
            align_row.append(align_map[align[idx]])
        else:
            align_row.append("---")

    lines = [_format_row(headers), _format_row(align_row)]
    for row in rows:
        if isinstance(row, dict):
            lines.append(_format_row([row.get(h, "") for h in headers]))
        elif isinstance(row, list):
            lines.append(_format_row(row))

    table = "\n".join(lines)
    return {"status": "ok", "error": None, "data": {"table": table}}
