import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    limit = params.get("limit", 10)
    try:
        limit = min(50, max(1, int(limit)))
    except (TypeError, ValueError):
        limit = 10
    try:
        r = requests.get(
            "https://api.rss2json.com/v1/api.json",
            params={"rss_url": "https://feeds.bbci.co.uk/news/world/rss.xml", "count": limit},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("status") != "ok":
            return _error("RSS fetch failed")
        items = j.get("items", [])[:limit]
        articles = [{"title": i.get("title"), "description": i.get("description"), "link": i.get("link"), "published": i.get("pubDate")} for i in items]
        data = {"articles": articles, "count": len(articles)}
        return {"status": "ok", "error": None, "data": data}
    except requests.RequestException as e:
        return _error(f"News fetch error: {e}")
