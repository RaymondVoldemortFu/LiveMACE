from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return "".join(self.parts)


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    html_text = params.get("html")
    if html_text is None:
        return _error("Missing required parameter: html")

    parser = _TextExtractor()
    parser.feed(str(html_text))
    text = parser.get_text()

    data = {"text": text}
    return {"status": "ok", "error": None, "data": data}
