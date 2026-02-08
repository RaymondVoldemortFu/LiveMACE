import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    topic = (params or {}).get("topic")
    if not topic or not isinstance(topic, str):
        return _error("Missing required parameter: topic")
    topic = topic.strip()
    if not topic:
        return _error("Missing required parameter: topic")
    if len(topic) > 50:
        return _error("Parameter topic exceeds 50 characters")

    client = get_openai_client()
    prompt = (
        "Generate exactly 3 article ideas for the given topic. "
        "Return JSON with keys: topic (string), topicIdeas (number), topics (array of 3 strings). "
        "Do not include extra keys."
    )
    user = f"Topic: {topic}"

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
    topics = data.get("topics") or []
    if len(topics) < 3:
        return _error("Failed to generate article ideas")

    return {
        "status": "ok",
        "error": None,
        "data": {
            "topic": topic,
            "topicIdeas": 3,
            "topics": topics[:3],
        },
    }
