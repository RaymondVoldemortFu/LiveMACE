import csv
import io


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize_bool(value, default=True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def run(params: dict) -> dict:
    params = params or {}
    csv_text = params.get("csv")
    delimiter = params.get("delimiter", ",")
    has_header = _normalize_bool(params.get("has_header"), True)

    if not csv_text or not isinstance(csv_text, str):
        return _error("Missing required parameter: csv")
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        return _error("Invalid delimiter: must be a single character")

    reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
    rows = [row for row in reader if row]
    if not rows:
        return _error("CSV is empty")

    if has_header:
        columns = rows[0]
        data_rows = rows[1:]
    else:
        columns = [f"col{i+1}" for i in range(len(rows[0]))]
        data_rows = rows

    json_rows = []
    for row in data_rows:
        padded = row + [""] * (len(columns) - len(row))
        json_rows.append({columns[i]: padded[i] for i in range(len(columns))})

    data = {
        "row_count": len(json_rows),
        "column_count": len(columns),
        "columns": columns,
        "json": json_rows,
    }
    return {"status": "ok", "error": None, "data": data}
