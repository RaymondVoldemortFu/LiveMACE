import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    zipcode = params.get("zipcode") or params.get("zip") or params.get("postal_code")
    country = params.get("country") or "US"
    if zipcode is None or str(zipcode).strip() == "":
        return _error("Missing required parameter: zipcode or zip")
    zipcode = str(zipcode).strip()
    country = str(country).strip().upper()[:2]
    try:
        r = requests.get(
            "https://api.zippopotam.us/" + country + "/" + zipcode,
            timeout=15,
        )
        if r.status_code != 200:
            data = {"zipcode": zipcode, "country": country, "place": None, "note": "No data for this zip"}
            return {"status": "ok", "error": None, "data": data}
        j = r.json()
    except requests.RequestException as e:
        return _error(str(e))
    places = j.get("places") or []
    if places and isinstance(places[0], dict):
        p = places[0]
        data = {
            "zipcode": zipcode,
            "country": j.get("country") or country,
            "country_code": j.get("country abbreviation"),
            "place_name": p.get("place name"),
            "state": p.get("state"),
            "state_abbr": p.get("state abbreviation"),
            "latitude": p.get("latitude"),
            "longitude": p.get("longitude"),
        }
    else:
        data = {"zipcode": zipcode, "country": country, "places": places}
    return {"status": "ok", "error": None, "data": data}
