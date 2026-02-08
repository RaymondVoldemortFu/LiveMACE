import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    acronym = (params or {}).get("acronym")
    context = (params or {}).get("context")
    if not acronym or not isinstance(acronym, str):
        return _error("Missing required parameter: acronym")
    acronym = acronym.strip()
    if not acronym:
        return _error("Missing required parameter: acronym")
    if len(acronym) > 20:
        return _error("Parameter acronym exceeds 20 characters")

    if not context or not isinstance(context, str):
        context = "General"
    context = context.strip() or "General"

    client = get_openai_client()
    prompt = (
        "You expand acronyms. Return JSON with keys: "
        "acronym (string), expansions (array of 1-3 objects), "
        "most_common (object), source (string), context_provided (string). "
        "Each expansion object must include expansion, description, category. "
        "Use the provided context for disambiguation. "
        "Do not include extra keys."
    )
    user = f"Acronym: {acronym}\nContext: {context}"

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or "{}"
    data = json.loads(content)

    expansions = data.get("expansions") or []
    if not expansions:
        return _error("Failed to generate expansions")

    most_common = data.get("most_common") or expansions[0]

    return {
        "status": "ok",
        "error": None,
        "data": {
            "acronym": acronym,
            "expansions": expansions,
            "most_common": most_common,
            "source": data.get("source") or "openai",
            "context_provided": context,
        },
    }
