import math

from apiverve_data import find_airport_by_iata, find_airport_by_icao


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def run(params: dict) -> dict:
    params = params or {}
    iata1 = params.get("iata1")
    iata2 = params.get("iata2")
    icao1 = params.get("icao1")
    icao2 = params.get("icao2")

    if iata1 and iata2:
        airport1 = find_airport_by_iata(str(iata1))
        airport2 = find_airport_by_iata(str(iata2))
    elif icao1 and icao2:
        airport1 = find_airport_by_icao(str(icao1))
        airport2 = find_airport_by_icao(str(icao2))
    else:
        return _error("Missing required parameters: iata1/iata2 or icao1/icao2")

    if not airport1 or not airport2:
        return _error("Airport not found")

    lat1 = airport1.get("lat")
    lon1 = airport1.get("lon")
    lat2 = airport2.get("lat")
    lon2 = airport2.get("lon")
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return _error("Airport coordinates are missing")

    distance_km = _haversine_km(lat1, lon1, lat2, lon2)
    distance_miles = distance_km * 0.621371

    return {
        "status": "ok",
        "error": None,
        "data": {
            "distanceMiles": round(distance_miles, 2),
            "distanceKm": round(distance_km, 2),
            "airport1": airport1,
            "airport2": airport2,
        },
    }
