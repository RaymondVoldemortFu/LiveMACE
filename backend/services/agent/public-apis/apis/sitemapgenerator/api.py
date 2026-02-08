from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    urls = params.get("urls") or params.get("urls_list") or []
    base_url = params.get("base_url") or params.get("base") or ""
    if not urls and not base_url:
        return _error("Missing required parameter: urls or base_url")
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split() if u.strip()]
    if base_url and not urls:
        urls = [base_url]
    if not urls:
        return _error("No URLs provided")
    base = base_url or (urls[0] if urls else "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entries = []
    for u in urls[:5000]:
        u = u.strip()
        if not u:
            continue
        if not u.startswith(("http://", "https://")):
            u = urljoin(base if base.startswith("http") else "https://" + base, u)
        entries.append({"loc": u, "lastmod": today})
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for e in entries:
        xml_lines.append("  <url>")
        xml_lines.append(f"    <loc>{e['loc']}</loc>")
        xml_lines.append(f"    <lastmod>{e['lastmod']}</lastmod>")
        xml_lines.append("  </url>")
    xml_lines.append("</urlset>")
    sitemap_xml = "\n".join(xml_lines)
    data = {"urls": [e["loc"] for e in entries], "count": len(entries), "sitemap_xml": sitemap_xml}
    return {"status": "ok", "error": None, "data": data}
