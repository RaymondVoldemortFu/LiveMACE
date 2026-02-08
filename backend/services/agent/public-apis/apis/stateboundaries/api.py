import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    state = params.get("state") or params.get("country") or params.get("region")
    country = params.get("country") or "USA"
    if not state or not str(state).strip():
        return _error("Missing required parameter: state or region")
    state = str(state).strip()
    try:
        r = requests.get(
            "https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json",
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    features = j.get("features") or []
    for f in features:
        props = f.get("properties") or {}
        if props.get("name") == state or props.get("STATE") == state or str(props.get("id")) == state:
            data = {"state": state, "properties": props, "geometry": f.get("geometry"), "bounds": f.get("bbox")}
            return {"status": "ok", "error": None, "data": data}
    data = {"state": state, "properties": None, "geometry": None, "note": "State not found in US dataset"}
    return {"status": "ok", "error": None, "data": data}
