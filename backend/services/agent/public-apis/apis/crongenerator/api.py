import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
    raise ValueError("Invalid JSON")


def run(params: dict) -> dict:
    params = params or {}
    schedule = params.get("schedule")
    if not schedule or not isinstance(schedule, str):
        return _error("Missing required parameter: schedule")

    schedule = schedule.strip()
    if len(schedule) > 200:
        return _error("Invalid schedule: must be 200 characters or less")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Convert the following natural language schedule into a standard 5-field cron expression "
        "(minute hour day-of-month month day-of-week). Respond ONLY with JSON using keys: "
        "expression, description. Schedule: "
        f"{schedule}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        content = response.choices[0].message.content or ""
        parsed = _extract_json(content)
    except Exception as exc:
        return _error(f"Cron generator error: {exc}")

    expression = parsed.get("expression")
    description = parsed.get("description")
    if not expression or not description:
        return _error("Cron generator returned invalid response")

    return {
        "status": "ok",
        "error": None,
        "data": {"schedule": schedule, "expression": expression, "description": description},
    }
