from apiverve_data import find_airport_by_iata, find_airport_by_icao


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    iata = params.get("iata")
    icao = params.get("icao")

    if iata:
        airport = find_airport_by_iata(str(iata))
    elif icao:
        airport = find_airport_by_icao(str(icao))
    else:
        return _error("Missing required parameter: iata or icao")

    if not airport:
        return _error("Airport not found")

    return {"status": "ok", "error": None, "data": airport}
