import json

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    port = params.get("port") or params.get("port_number")
    if port is None:
        return _error("Missing required parameter: port")
    try:
        p = int(port)
        if not 0 <= p <= 65535:
            return _error("Port must be 0-65535")
    except (TypeError, ValueError):
        return _error("Invalid port number")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = (
        f"What is TCP/UDP port {p} commonly used for? "
        "Respond with a JSON object with keys: \"service\" (short name, e.g. HTTP), \"protocol\" (TCP or UDP or both), \"description\" (one short sentence). "
        "If the port is not well-known, set service to \"unknown\" and description to a brief note."
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        return _error(f"Port lookup error: {exc}")

    service = protocol = description = ""
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            service = result.get("service") or result.get("name") or ""
            protocol = result.get("protocol") or "TCP"
            description = result.get("description") or ""
    except Exception:
        pass

    data = {"port": p, "service": service or "unknown", "protocol": protocol, "description": description}
    return {"status": "ok", "error": None, "data": data}
