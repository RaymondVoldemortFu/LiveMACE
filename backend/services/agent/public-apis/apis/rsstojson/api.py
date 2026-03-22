import xml.etree.ElementTree as ET
import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    url = params.get("url") or params.get("rss_url") or params.get("feed")
    if not url or not str(url).strip():
        return _error("Missing required parameter: url or rss_url")
    url = str(url).strip()
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except requests.RequestException as e:
        return _error(f"Fetch error: {e}")
    except ET.ParseError as e:
        return _error(f"Invalid XML: {e}")
    ns = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/", "content": "http://purl.org/rss/1.0/modules/content/"}
    channel = root.find("channel") or root.find("atom:channel", ns) or root
    title = channel.find("title")
    title = title.text if title is not None else None
    items = []
    for item in root.findall(".//item") or root.findall(".//atom:entry", ns):
        entry = {}
        for tag in ["title", "link", "description", "pubDate", "guid"]:
            el = item.find(tag) or item.find(f"atom:{tag}", ns)
            if el is not None and el.text:
                entry[tag] = el.text
        if entry:
            items.append(entry)
    data = {"url": url, "title": title, "items": items, "count": len(items)}
    return {"status": "ok", "error": None, "data": data}
