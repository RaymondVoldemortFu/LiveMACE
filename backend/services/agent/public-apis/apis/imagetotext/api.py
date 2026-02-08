import base64

from config import OPENAI_MODEL, get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _extract_image(params: dict):
    for key in ("image_base64", "image", "data"):
        value = params.get(key)
        if value:
            return value
    return None


def run(params: dict) -> dict:
    params = params or {}
    image_b64 = _extract_image(params)
    if not image_b64 or not isinstance(image_b64, str):
        return _error("Missing required parameter: image_base64")

    try:
        base64.b64decode(image_b64, validate=True)
    except Exception:
        return _error("Invalid base64 image data")

    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    prompt = "Extract all visible text from the image. Return plain text only."
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_base64": image_b64},
                    ],
                }
            ],
            temperature=0.0,
        )
        content = response.output_text or ""
    except Exception as exc:
        return _error(f"Image to text error: {exc}")

    data = {"text": content.strip()}
    return {"status": "ok", "error": None, "data": data}
