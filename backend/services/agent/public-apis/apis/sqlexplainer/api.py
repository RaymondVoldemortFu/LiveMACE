import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    sql = params.get("sql") or params.get("query")
    if sql is None or not str(sql).strip():
        return _error("Missing required parameter: sql or query")
    sql = str(sql).strip()

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        "Explain what this SQL query does in 2-4 short sentences (plain English). "
        "Mention tables, filters, and purpose. Do not execute it."
        f"\n\nSQL: {sql}"
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        explanation = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"SQL explainer error: {exc}")

    data = {"sql": sql, "explanation": explanation}
    return {"status": "ok", "error": None, "data": data}
