DAY_OF_WEEK_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}
MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize_value(value: str, name_map: dict, min_val: int, max_val: int) -> int:
    raw = value.strip().lower()
    if name_map and raw in name_map:
        return name_map[raw]
    if raw.isdigit():
        return int(raw)
    raise ValueError("Invalid field value")


def _expand_range(start: int, end: int, step: int, min_val: int, max_val: int) -> list:
    if step <= 0:
        raise ValueError("Invalid step")
    if start < min_val or end > max_val or start > end:
        raise ValueError("Invalid range")
    return list(range(start, end + 1, step))


def _parse_part(part: str, name_map: dict, min_val: int, max_val: int) -> list:
    if part in {"*", "?"}:
        return list(range(min_val, max_val + 1))

    if "/" in part:
        base, step_raw = part.split("/", 1)
        step = int(step_raw)
        if base in {"*", "?"}:
            return _expand_range(min_val, max_val, step, min_val, max_val)
        if "-" in base:
            start_raw, end_raw = base.split("-", 1)
            start = _normalize_value(start_raw, name_map, min_val, max_val)
            end = _normalize_value(end_raw, name_map, min_val, max_val)
            return _expand_range(start, end, step, min_val, max_val)
        start = _normalize_value(base, name_map, min_val, max_val)
        return _expand_range(start, max_val, step, min_val, max_val)

    if "-" in part:
        start_raw, end_raw = part.split("-", 1)
        start = _normalize_value(start_raw, name_map, min_val, max_val)
        end = _normalize_value(end_raw, name_map, min_val, max_val)
        return _expand_range(start, end, 1, min_val, max_val)

    single = _normalize_value(part, name_map, min_val, max_val)
    return [single]


def _parse_field(expression: str, name_map: dict, min_val: int, max_val: int) -> dict:
    values = set()
    parts = [p.strip() for p in expression.split(",") if p.strip()]
    if not parts:
        raise ValueError("Empty field")
    for part in parts:
        values.update(_parse_part(part, name_map, min_val, max_val))

    normalized = sorted(values)
    description = "Every"
    if len(normalized) == 1:
        description = f"At {normalized[0]}"
    elif expression not in {"*", "?"}:
        description = f"At {', '.join(str(v) for v in normalized)}"

    return {"expression": expression, "description": description, "values": normalized}


def _frequency(fields: dict) -> dict:
    if fields["dayOfWeek"]["expression"] not in {"*", "?"}:
        return {"type": "Weekly", "interval": "week"}
    if fields["dayOfMonth"]["expression"] not in {"*", "?"}:
        return {"type": "Monthly", "interval": "month"}
    if fields["month"]["expression"] not in {"*", "?"}:
        return {"type": "Yearly", "interval": "year"}
    return {"type": "Daily", "interval": "day"}


def run(params: dict) -> dict:
    params = params or {}
    expression = params.get("expression")

    if not expression or not isinstance(expression, str):
        return _error("Missing required parameter: expression")

    fields = [f for f in expression.split() if f]
    if len(fields) not in {5, 6}:
        return _error("Invalid cron expression: must be 5 or 6 fields")

    try:
        if len(fields) == 5:
            second_expr = "0"
            minute_expr, hour_expr, dom_expr, month_expr, dow_expr = fields
            format_name = "5-field"
        else:
            second_expr, minute_expr, hour_expr, dom_expr, month_expr, dow_expr = fields
            format_name = "6-field"

        parsed = {
            "second": _parse_field(second_expr, None, 0, 59),
            "minute": _parse_field(minute_expr, None, 0, 59),
            "hour": _parse_field(hour_expr, None, 0, 23),
            "dayOfMonth": _parse_field(dom_expr, None, 1, 31),
            "month": _parse_field(month_expr, MONTH_NAMES, 1, 12),
            "dayOfWeek": _parse_field(dow_expr, DAY_OF_WEEK_NAMES, 0, 7),
        }
        if 7 in parsed["dayOfWeek"]["values"]:
            parsed["dayOfWeek"]["values"] = [0 if v == 7 else v for v in parsed["dayOfWeek"]["values"]]
            parsed["dayOfWeek"]["values"].sort()
    except ValueError as exc:
        return _error(f"Invalid cron expression: {exc}")

    data = {
        "expression": expression,
        "isValid": True,
        "format": format_name,
        "fields": parsed,
        "description": "Cron expression",
        "frequency": _frequency(parsed),
    }
    return {"status": "ok", "error": None, "data": data}
