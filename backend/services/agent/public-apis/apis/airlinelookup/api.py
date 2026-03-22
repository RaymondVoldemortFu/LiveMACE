from apiverve_data import find_airline_by_iata, find_airline_by_icao, find_airline_by_name


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    iata = params.get("iata")
    icao = params.get("icao")
    name = params.get("name")

    if iata:
        results = find_airline_by_iata(str(iata))
    elif icao:
        results = find_airline_by_icao(str(icao))
    elif name:
        results = find_airline_by_name(str(name))
    else:
        return _error("Missing required parameter: iata, icao, or name")

    return {"status": "ok", "error": None, "data": results}
