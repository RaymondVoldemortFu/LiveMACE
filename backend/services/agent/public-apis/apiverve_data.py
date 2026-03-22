import csv
import io
from functools import lru_cache
from typing import Dict, List, Optional

import requests


AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
AIRPORTS_URL = "https://ourairports.com/data/airports.csv"


US_STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


def _download_text(url: str, timeout: int = 30) -> str:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def _clean_value(value: str) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value or value == "\\N":
        return None
    return value


def _state_from_region(iso_region: Optional[str]) -> Optional[str]:
    if not iso_region:
        return None
    if "-" not in iso_region:
        return iso_region
    _, region = iso_region.split("-", 1)
    return US_STATE_NAMES.get(region, region)


@lru_cache(maxsize=1)
def load_airlines() -> List[Dict[str, Optional[str]]]:
    raw = _download_text(AIRLINES_URL)
    reader = csv.reader(io.StringIO(raw))
    airlines = []
    for row in reader:
        if len(row) < 8:
            continue
        name = _clean_value(row[1])
        alias = _clean_value(row[2])
        iata = _clean_value(row[3])
        icao = _clean_value(row[4])
        callsign = _clean_value(row[5])
        country = _clean_value(row[6])
        if not name:
            continue
        airlines.append(
            {
                "name": name,
                "alias": alias,
                "iata": iata,
                "icao": icao,
                "callsign": callsign,
                "country": country,
                "id": iata or icao or name,
            }
        )
    return airlines


@lru_cache(maxsize=1)
def load_airports() -> List[Dict[str, Optional[str]]]:
    raw = _download_text(AIRPORTS_URL)
    reader = csv.DictReader(io.StringIO(raw))
    airports = []
    for row in reader:
        iata = _clean_value(row.get("iata_code", ""))
        icao = _clean_value(row.get("icao_code", ""))
        if not iata and not icao:
            continue
        name = _clean_value(row.get("name", ""))
        city = _clean_value(row.get("municipality", ""))
        country = _clean_value(row.get("iso_country", ""))
        state = _state_from_region(_clean_value(row.get("iso_region", "")))
        tz = _clean_value(row.get("timezone", ""))
        elevation = _clean_value(row.get("elevation_ft", ""))
        lat = _clean_value(row.get("latitude_deg", ""))
        lon = _clean_value(row.get("longitude_deg", ""))
        airports.append(
            {
                "icao": icao,
                "iata": iata,
                "name": name,
                "city": city,
                "state": state,
                "country": country,
                "elevation": float(elevation) if elevation else None,
                "lat": float(lat) if lat else None,
                "lon": float(lon) if lon else None,
                "tz": tz,
                "city_info": {
                    "name": city or "",
                    "altName": "",
                    "country": country or "",
                },
            }
        )
    return airports


def find_airline_by_iata(iata: str) -> List[Dict[str, Optional[str]]]:
    iata = iata.strip().upper()
    return [a for a in load_airlines() if a.get("iata", "").upper() == iata]


def find_airline_by_icao(icao: str) -> List[Dict[str, Optional[str]]]:
    icao = icao.strip().upper()
    return [a for a in load_airlines() if a.get("icao", "").upper() == icao]


def find_airline_by_name(name: str) -> List[Dict[str, Optional[str]]]:
    name = name.strip().lower()
    results = []
    for airline in load_airlines():
        airline_name = (airline.get("name") or "").lower()
        alias = (airline.get("alias") or "").lower()
        if name in airline_name or (alias and name in alias):
            results.append(airline)
    return results


def find_airport_by_iata(iata: str) -> Optional[Dict[str, Optional[str]]]:
    iata = iata.strip().upper()
    for airport in load_airports():
        if (airport.get("iata") or "").upper() == iata:
            return airport
    return None


def find_airport_by_icao(icao: str) -> Optional[Dict[str, Optional[str]]]:
    icao = icao.strip().upper()
    for airport in load_airports():
        if (airport.get("icao") or "").upper() == icao:
            return airport
    return None
