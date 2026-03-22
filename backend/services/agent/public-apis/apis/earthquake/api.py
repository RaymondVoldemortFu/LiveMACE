import datetime as _dt

import requests


USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _to_iso(ms: int) -> str:
    try:
        return _dt.datetime.utcfromtimestamp(ms / 1000).isoformat() + "Z"
    except Exception:
        return None


def run(params: dict) -> dict:
    try:
        response = requests.get(USGS_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Earthquake data error: {exc}")
    except ValueError:
        return _error("Earthquake data returned invalid JSON")

    features = payload.get("features") or []
    events = []
    for item in features:
        props = item.get("properties") or {}
        geom = item.get("geometry") or {}
        coords = geom.get("coordinates") or []
        events.append(
            {
                "id": item.get("id"),
                "place": props.get("place"),
                "magnitude": props.get("mag"),
                "time": _to_iso(props.get("time")),
                "updated": _to_iso(props.get("updated")),
                "url": props.get("url"),
                "tsunami": bool(props.get("tsunami")),
                "coordinates": {
                    "longitude": coords[0] if len(coords) > 0 else None,
                    "latitude": coords[1] if len(coords) > 1 else None,
                    "depth_km": coords[2] if len(coords) > 2 else None,
                },
            }
        )

    data = {"count": len(events), "events": events, "source": "USGS"}
    return {"status": "ok", "error": None, "data": data}
