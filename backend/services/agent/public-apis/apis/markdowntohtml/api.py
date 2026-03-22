import html
import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")

    lines = str(text).splitlines()
    rendered = []
    for line in lines:
        escaped = html.escape(line)
        if escaped.startswith("#"):
            level = len(escaped) - len(escaped.lstrip("#"))
            level = min(level, 6)
            content = escaped[level:].strip()
            rendered.append(f"<h{level}>{content}</h{level}>")
            continue
        rendered.append(f"<p>{escaped}</p>")

    html_text = "\n".join(rendered)
    html_text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_text)
    html_text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html_text)
    html_text = re.sub(r"`(.+?)`", r"<code>\1</code>", html_text)
    html_text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html_text)

    return {"status": "ok", "error": None, "data": {"html": html_text}}
