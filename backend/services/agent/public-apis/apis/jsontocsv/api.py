import csv
import io
import json


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    data = params.get("data")
    delimiter = params.get("delimiter", ",")

    if data is None:
        return _error("Missing required parameter: data")

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return _error("Invalid JSON data")

    if not isinstance(data, list):
        return _error("Data must be a JSON array")

    output = io.StringIO()
    if not data:
        return {"status": "ok", "error": None, "data": {"csv": ""}}

    headers = sorted({k for row in data if isinstance(row, dict) for k in row.keys()})
    writer = csv.DictWriter(output, fieldnames=headers, delimiter=delimiter)
    writer.writeheader()
    for row in data:
        if isinstance(row, dict):
            writer.writerow(row)
    csv_text = output.getvalue()

    return {"status": "ok", "error": None, "data": {"csv": csv_text}}
