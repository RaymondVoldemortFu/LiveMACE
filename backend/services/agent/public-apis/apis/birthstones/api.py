from functools import lru_cache

import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


MONTHS = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

MODERN_STONES = {
    1: ["Garnet"],
    2: ["Amethyst"],
    3: ["Aquamarine"],
    4: ["Diamond"],
    5: ["Emerald"],
    6: ["Pearl", "Alexandrite", "Moonstone"],
    7: ["Ruby"],
    8: ["Peridot", "Spinel", "Sardonyx"],
    9: ["Sapphire"],
    10: ["Opal", "Tourmaline"],
    11: ["Topaz", "Citrine"],
    12: ["Turquoise", "Zircon", "Tanzanite"],
}

TRADITIONAL_STONES = {
    1: ["Garnet"],
    2: ["Amethyst"],
    3: ["Bloodstone"],
    4: ["Diamond"],
    5: ["Emerald"],
    6: ["Pearl", "Moonstone"],
    7: ["Ruby"],
    8: ["Sardonyx"],
    9: ["Sapphire"],
    10: ["Opal"],
    11: ["Topaz"],
    12: ["Turquoise"],
}

ZODIAC_STONES = {
    "aries": ["Diamond"],
    "taurus": ["Emerald"],
    "gemini": ["Pearl"],
    "cancer": ["Ruby"],
    "leo": ["Peridot"],
    "virgo": ["Sapphire"],
    "libra": ["Opal"],
    "scorpio": ["Topaz"],
    "sagittarius": ["Turquoise"],
    "capricorn": ["Garnet"],
    "aquarius": ["Amethyst"],
    "pisces": ["Aquamarine"],
}

GEMSTONES_URL = "https://raw.githubusercontent.com/sarahelizadowd/jsonsforfun/master/Gemstones.json"


@lru_cache(maxsize=1)
def _load_gemstones() -> dict:
    response = requests.get(GEMSTONES_URL, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Invalid gemstones dataset")
    return payload


def _normalize_month(value) -> int:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
        for num, name in MONTHS.items():
            if name.lower() == value.lower():
                return num
    raise ValueError("Invalid month")


def _stone_details(name: str) -> dict:
    try:
        data = _load_gemstones()
    except (requests.RequestException, ValueError):
        data = {}

    for item in data.values():
        if str(item.get("gemstone_name", "")).lower() == name.lower():
            return {
                "name": item.get("gemstone_name"),
                "alternative_name": item.get("alternative_name"),
                "color": item.get("colour"),
                "meaning": item.get("description_of_benefits"),
                "important_fact": item.get("important_fact"),
            }
    return {"name": name, "alternative_name": None, "color": None, "meaning": None, "important_fact": None}


def _match_stone(query: str) -> str:
    if not query:
        return None
    return query.strip().lower()


def run(params: dict) -> dict:
    params = params or {}
    month = params.get("month")
    zodiac = params.get("zodiac")
    stone = params.get("stone")

    if month is None and zodiac is None and stone is None:
        return _error("Missing required parameter: month, zodiac, or stone")

    if month is not None:
        try:
            month_num = _normalize_month(month)
        except ValueError:
            return _error("Invalid month: use 1-12 or month name")
        if month_num not in MONTHS:
            return _error("Invalid month: use 1-12")
        modern = MODERN_STONES.get(month_num, [])
        traditional = TRADITIONAL_STONES.get(month_num, [])
        zodiac_list = [z for z, stones in ZODIAC_STONES.items() if any(s in modern for s in stones)]
        stones = [_stone_details(s) for s in modern] + [
            _stone_details(s) for s in traditional if s not in modern
        ]
        data = {
            "month": MONTHS[month_num],
            "monthNumber": month_num,
            "modern": modern,
            "traditional": traditional,
            "zodiac": zodiac_list,
            "stones": stones,
        }
        return {"status": "ok", "error": None, "data": data}

    if zodiac is not None:
        zodiac_key = str(zodiac).strip().lower()
        stones = ZODIAC_STONES.get(zodiac_key)
        if not stones:
            return _error("Unknown zodiac sign")
        data = {
            "zodiac": zodiac_key,
            "stones": stones,
            "details": [_stone_details(s) for s in stones],
        }
        return {"status": "ok", "error": None, "data": data}

    stone_key = _match_stone(str(stone))
    months = []
    for month_num, stones in MODERN_STONES.items():
        if any(s.lower() == stone_key for s in stones):
            months.append(MONTHS[month_num])
    for month_num, stones in TRADITIONAL_STONES.items():
        if any(s.lower() == stone_key for s in stones) and MONTHS[month_num] not in months:
            months.append(MONTHS[month_num])
    zodiacs = [z for z, stones in ZODIAC_STONES.items() if any(s.lower() == stone_key for s in stones)]
    data = {
        "stone": stone,
        "months": months,
        "zodiac": zodiacs,
        "details": _stone_details(stone),
    }
    return {"status": "ok", "error": None, "data": data}
