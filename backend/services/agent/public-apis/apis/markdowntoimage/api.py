from config import get_openai_client


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    markdown_text = params.get("text")
    if markdown_text is None:
        return _error("Missing required parameter: text")

    prompt = (
        "Render the following markdown as a clean, readable image on a white background. "
        "Return only the image."
    )
    try:
        client = get_openai_client()
    except ValueError as exc:
        return _error(str(exc))

    try:
        response = client.images.generate(
            model="gpt-image-1",
            prompt=f"{prompt}\n\n{markdown_text}",
            size="1024x1024",
        )
        image_b64 = response.data[0].b64_json
    except Exception as exc:
        return _error(f"Markdown to image error: {exc}")

    data = {"image_base64": image_b64}
    return {"status": "ok", "error": None, "data": data}
