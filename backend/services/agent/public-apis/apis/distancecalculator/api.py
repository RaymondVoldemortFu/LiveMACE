import math

import requests


NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def _reverse_geocode(lat: float, lon: float) -> dict:
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 10},
            headers={"User-Agent": "apiverve-local"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return {}
    except ValueError:
        return {}

    address = payload.get("address") or {}
    return {
        "city": address.get("city") or address.get("town") or address.get("village"),
        "state": address.get("state"),
    }


def run(params: dict) -> dict:
    params = params or {}
    lat1 = params.get("lat1")
    lon1 = params.get("lon1")
    lat2 = params.get("lat2")
    lon2 = params.get("lon2")

    if None in (lat1, lon1, lat2, lon2):
        return _error("Missing required parameters: lat1, lon1, lat2, lon2")

    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
    except (TypeError, ValueError):
        return _error("Invalid coordinates: lat/lon must be numbers")

    distance_km = _haversine(lat1, lon1, lat2, lon2)
    distance_miles = distance_km * 0.621371

    loc1 = _reverse_geocode(lat1, lon1)
    loc2 = _reverse_geocode(lat2, lon2)

    data = {
        "distanceMiles": distance_miles,
        "distanceKm": distance_km,
        "location1": {
            "latitude": str(lat1),
            "longitude": str(lon1),
            "city": loc1.get("city"),
            "state": loc1.get("state"),
        },
        "location2": {
            "latitude": str(lat2),
            "longitude": str(lon2),
            "city": loc2.get("city"),
            "state": loc2.get("state"),
        },
    }
    return {"status": "ok", "error": None, "data": data}
