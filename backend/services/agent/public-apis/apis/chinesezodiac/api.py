def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


_START_DATE = (1900, 1, 31)

_YEAR_INFOS = [
    0x04BD8,
    0x04AE0,
    0x0A570,
    0x054D5,
    0x0D260,
    0x0D950,
    0x16554,
    0x056A0,
    0x09AD0,
    0x055D2,
    0x04AE0,
    0x0A5B6,
    0x0A4D0,
    0x0D250,
    0x1D255,
    0x0B540,
    0x0D6A0,
    0x0ADA2,
    0x095B0,
    0x14977,
    0x04970,
    0x0A4B0,
    0x0B4B5,
    0x06A50,
    0x06D40,
    0x1AB54,
    0x02B60,
    0x09570,
    0x052F2,
    0x04970,
    0x06566,
    0x0D4A0,
    0x0EA50,
    0x06E95,
    0x05AD0,
    0x02B60,
    0x186E3,
    0x092E0,
    0x1C8D7,
    0x0C950,
    0x0D4A0,
    0x1D8A6,
    0x0B550,
    0x056A0,
    0x1A5B4,
    0x025D0,
    0x092D0,
    0x0D2B2,
    0x0A950,
    0x0B557,
    0x06CA0,
    0x0B550,
    0x15355,
    0x04DA0,
    0x0A5D0,
    0x14573,
    0x052B0,
    0x0A9A8,
    0x0E950,
    0x06AA0,
    0x0AEA6,
    0x0AB50,
    0x04B60,
    0x0AAE4,
    0x0A570,
    0x05260,
    0x0F263,
    0x0D950,
    0x05B57,
    0x056A0,
    0x096D0,
    0x04DD5,
    0x04AD0,
    0x0A4D0,
    0x0D4D4,
    0x0D250,
    0x0D558,
    0x0B540,
    0x0B5A0,
    0x195A6,
    0x095B0,
    0x049B0,
    0x0A974,
    0x0A4B0,
    0x0B27A,
    0x06A50,
    0x06D40,
    0x0AF46,
    0x0AB60,
    0x09570,
    0x04AF5,
    0x04970,
    0x064B0,
    0x074A3,
    0x0EA50,
    0x06B58,
    0x05AC0,
    0x0AB60,
    0x096D5,
    0x092E0,
    0x0C960,
    0x0D954,
    0x0D4A0,
    0x0DA50,
    0x07552,
    0x056A0,
    0x0ABB7,
    0x025D0,
    0x092D0,
    0x0CAB5,
    0x0A950,
    0x0B4A0,
    0x0BAA4,
    0x0AD50,
    0x055D9,
    0x04BA0,
    0x0A5B0,
    0x15176,
    0x052B0,
    0x0A930,
    0x07954,
    0x06AA0,
    0x0AD50,
    0x05B52,
    0x04B60,
    0x0A6E6,
    0x0A4E0,
    0x0D260,
    0x0EA65,
    0x0D530,
    0x05AA0,
    0x076A3,
    0x096D0,
    0x04AFB,
    0x04AD0,
    0x0A4D0,
    0x1D0B6,
    0x0D250,
    0x0D520,
    0x0DD45,
    0x0B5A0,
    0x056D0,
    0x055B2,
    0x049B0,
    0x0A577,
    0x0A4B0,
    0x0AA50,
    0x1B255,
    0x06D20,
    0x0ADA0,
    0x14B63,
    0x09370,
    0x049F8,
    0x04970,
    0x064B0,
    0x168A6,
    0x0EA50,
    0x06AA0,
    0x1A6C4,
    0x0AAE0,
    0x092E0,
    0x0D2E3,
    0x0C960,
    0x0D557,
    0x0D4A0,
    0x0DA50,
    0x05D55,
    0x056A0,
    0x0A6D0,
    0x055D4,
    0x052D0,
    0x0A9B8,
    0x0A950,
    0x0B4A0,
    0x0B6A6,
    0x0AD50,
    0x055A0,
    0x0ABA4,
    0x0A5B0,
    0x052B0,
    0x0B273,
    0x06930,
    0x07337,
    0x06AA0,
    0x0AD50,
    0x14B55,
    0x04B60,
    0x0A570,
    0x054E4,
    0x0D160,
    0x0E968,
    0x0D520,
    0x0DAA0,
    0x16AA6,
    0x056D0,
    0x04AE0,
    0x0A9D4,
    0x0A2D0,
    0x0D150,
    0x0F252,
]

_ANIMALS = [
    ("Rat", "鼠"),
    ("Ox", "牛"),
    ("Tiger", "虎"),
    ("Rabbit", "兔"),
    ("Dragon", "龙"),
    ("Snake", "蛇"),
    ("Horse", "马"),
    ("Goat", "羊"),
    ("Monkey", "猴"),
    ("Rooster", "鸡"),
    ("Dog", "狗"),
    ("Pig", "猪"),
]

_ELEMENTS = [
    ("Wood", "木", "Green", "Yang"),
    ("Wood", "木", "Green", "Yin"),
    ("Fire", "火", "Red", "Yang"),
    ("Fire", "火", "Red", "Yin"),
    ("Earth", "土", "Yellow/Brown", "Yang"),
    ("Earth", "土", "Yellow/Brown", "Yin"),
    ("Metal", "金", "White", "Yang"),
    ("Metal", "金", "White", "Yin"),
    ("Water", "水", "Black", "Yang"),
    ("Water", "水", "Black", "Yin"),
]

_TRAITS = {
    "Rat": ["Quick-witted", "Resourceful", "Versatile", "Smart"],
    "Ox": ["Diligent", "Dependable", "Strong", "Determined"],
    "Tiger": ["Brave", "Confident", "Competitive", "Unpredictable"],
    "Rabbit": ["Gentle", "Quiet", "Elegant", "Alert"],
    "Dragon": ["Confident", "Intelligent", "Enthusiastic", "Ambitious"],
    "Snake": ["Enigmatic", "Intelligent", "Wise", "Decisive"],
    "Horse": ["Animated", "Active", "Energetic", "Independent"],
    "Goat": ["Calm", "Gentle", "Sympathetic", "Creative"],
    "Monkey": ["Sharp", "Smart", "Curious", "Mischievous"],
    "Rooster": ["Observant", "Hardworking", "Courageous", "Talented"],
    "Dog": ["Loyal", "Honest", "Prudent", "Kind"],
    "Pig": ["Compassionate", "Generous", "Diligent", "Sincere"],
}


def _year_info_to_days(year_info: int) -> int:
    year_info = int(year_info)
    days = 29 * 12
    leap = year_info % 16 != 0
    if leap:
        days += 29
    year_info //= 16
    for _ in range(12 + int(leap)):
        if year_info % 2 == 1:
            days += 1
        year_info //= 2
    return days


_YEAR_DAYS = [_year_info_to_days(info) for info in _YEAR_INFOS]


def _chinese_new_year_date(year: int) -> str:
    if year < 1900 or year >= 1900 + len(_YEAR_INFOS):
        raise ValueError("year out of range [1900, 2100)")
    offset = sum(_YEAR_DAYS[: year - 1900])
    base_year, base_month, base_day = _START_DATE
    from datetime import date, timedelta

    return (date(base_year, base_month, base_day) + timedelta(days=offset)).isoformat()


def _zodiac_from_year(zodiac_year: int) -> dict:
    animal_idx = (zodiac_year - 4) % 12
    element_idx = (zodiac_year - 4) % 10
    animal, animal_cn = _ANIMALS[animal_idx]
    element, element_cn, color, polarity = _ELEMENTS[element_idx]
    return {
        "animal": animal,
        "animalChinese": animal_cn,
        "element": element,
        "elementChinese": element_cn,
        "elementColor": color,
        "polarity": polarity,
        "traits": _TRAITS.get(animal, []),
        "sexagenaryCyclePosition": (zodiac_year - 1984) % 60 + 1,
        "fullName": f"{element} {animal}",
    }


def run(params: dict) -> dict:
    params = params or {}
    date = params.get("date")
    animal = params.get("animal")

    if date is None and animal is None:
        return _error("Missing required parameter: date or animal")

    if date is not None:
        if not isinstance(date, str):
            return _error("Invalid date: must be YYYY-MM-DD")
        from datetime import date as dt_date

        try:
            parsed = dt_date.fromisoformat(date)
        except ValueError:
            return _error("Invalid date: must be YYYY-MM-DD")

        if parsed.year < 1900 or parsed.year >= 1900 + len(_YEAR_INFOS):
            return _error("Date out of supported range: 1900-2099")

        cny = _chinese_new_year_date(parsed.year)
        zodiac_year = parsed.year - 1 if parsed.isoformat() < cny else parsed.year
        zodiac = _zodiac_from_year(zodiac_year)

        return {
            "status": "ok",
            "error": None,
            "data": {
                "date": parsed.isoformat(),
                "zodiacYear": zodiac_year,
                "animal": zodiac["animal"],
                "animalChinese": zodiac["animalChinese"],
                "element": zodiac["element"],
                "elementChinese": zodiac["elementChinese"],
                "elementColor": zodiac["elementColor"],
                "polarity": zodiac["polarity"],
                "traits": zodiac["traits"],
                "sexagenaryCyclePosition": zodiac["sexagenaryCyclePosition"],
                "fullName": zodiac["fullName"],
                "chineseNewYear": cny,
            },
        }

    if not isinstance(animal, str):
        return _error("Invalid animal: must be a string")

    animal_key = animal.strip().lower()
    animal_map = {name.lower(): name for name, _ in _ANIMALS}
    if animal_key not in animal_map:
        return _error("Invalid animal: must be one of rat, ox, tiger, rabbit, dragon, snake, horse, goat, monkey, rooster, dog, pig")

    normalized = animal_map[animal_key]
    years = []
    for year in range(1900, 1900 + len(_YEAR_INFOS)):
        if _zodiac_from_year(year)["animal"] == normalized:
            years.append(year)

    return {
        "status": "ok",
        "error": None,
        "data": {"animal": normalized, "years": years},
    }
