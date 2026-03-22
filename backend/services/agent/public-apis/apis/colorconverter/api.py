import colorsys


CSS_COLORS = {
    "black": "000000",
    "white": "FFFFFF",
    "red": "FF0000",
    "green": "008000",
    "blue": "0000FF",
    "yellow": "FFFF00",
    "cyan": "00FFFF",
    "magenta": "FF00FF",
    "gray": "808080",
    "grey": "808080",
    "orange": "FFA500",
    "purple": "800080",
    "pink": "FFC0CB",
    "brown": "A52A2A",
    "teal": "008080",
    "navy": "000080",
    "maroon": "800000",
    "olive": "808000",
    "silver": "C0C0C0",
    "lime": "00FF00",
    "gold": "FFD700",
    "coral": "FF7F50",
    "tomato": "FF6347",
    "chocolate": "D2691E",
    "salmon": "FA8072",
    "indigo": "4B0082",
    "violet": "EE82EE",
}

ANSI16_COLORS = [
    (0, 0, 0, 30),
    (205, 0, 0, 31),
    (0, 205, 0, 32),
    (205, 205, 0, 33),
    (0, 0, 238, 34),
    (205, 0, 205, 35),
    (0, 205, 205, 36),
    (229, 229, 229, 37),
    (127, 127, 127, 90),
    (255, 0, 0, 91),
    (0, 255, 0, 92),
    (255, 255, 0, 93),
    (92, 92, 255, 94),
    (255, 0, 255, 95),
    (0, 255, 255, 96),
    (255, 255, 255, 97),
]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_hex(value: str) -> str:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError("Invalid hex color")
    return value.upper()


def _hex_to_rgb_tuple(hex_value: str) -> tuple:
    return int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16)


def _rgb_to_hex(rgb: tuple) -> str:
    return "{:02X}{:02X}{:02X}".format(*rgb)


def _rgb_to_hsl(rgb: tuple) -> tuple:
    r, g, b = [c / 255.0 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360, 1), round(s * 100, 1), round(l * 100, 1)


def _hsl_to_rgb(hsl: tuple) -> tuple:
    h, s, l = hsl
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return round(r * 255), round(g * 255), round(b * 255)


def _cmyk_to_rgb(cmyk: tuple) -> tuple:
    c, m, y, k = cmyk
    c /= 100.0
    m /= 100.0
    y /= 100.0
    k /= 100.0
    r = round(255 * (1 - c) * (1 - k))
    g = round(255 * (1 - m) * (1 - k))
    b = round(255 * (1 - y) * (1 - k))
    return r, g, b


def _rgb_to_cmyk(rgb: tuple) -> tuple:
    r, g, b = [c / 255.0 for c in rgb]
    k = 1 - max(r, g, b)
    if k == 1:
        return 0, 0, 0, 100
    c = (1 - r - k) / (1 - k)
    m = (1 - g - k) / (1 - k)
    y = (1 - b - k) / (1 - k)
    return round(c * 100), round(m * 100), round(y * 100), round(k * 100)


def _nearest_ansi(rgb: tuple) -> int:
    best = None
    best_code = 30
    for r, g, b, code in ANSI16_COLORS:
        dist = (rgb[0] - r) ** 2 + (rgb[1] - g) ** 2 + (rgb[2] - b) ** 2
        if best is None or dist < best:
            best = dist
            best_code = code
    return best_code


def run(params: dict) -> dict:
    params = params or {}
    hex_value = params.get("hex")
    rgb_value = params.get("rgb")
    hsl_value = params.get("hsl")
    cmyk_value = params.get("cmyk")
    name_value = params.get("name")

    provided = [v for v in [hex_value, rgb_value, hsl_value, cmyk_value, name_value] if v is not None]
    if len(provided) != 1:
        return _error("Provide exactly one of: hex, rgb, hsl, cmyk, name")

    try:
        if hex_value is not None:
            hex_value = _parse_hex(str(hex_value))
            rgb = _hex_to_rgb_tuple(hex_value)
        elif rgb_value is not None:
            parts = [p.strip() for p in str(rgb_value).split(",")]
            if len(parts) != 3:
                raise ValueError("Invalid rgb")
            rgb = tuple(int(p) for p in parts)
            if any(c < 0 or c > 255 for c in rgb):
                raise ValueError("Invalid rgb")
            hex_value = _rgb_to_hex(rgb)
        elif hsl_value is not None:
            parts = [p.strip() for p in str(hsl_value).split(",")]
            if len(parts) != 3:
                raise ValueError("Invalid hsl")
            h, s, l = [float(p) for p in parts]
            rgb = _hsl_to_rgb((h, s, l))
            hex_value = _rgb_to_hex(rgb)
        elif cmyk_value is not None:
            parts = [p.strip() for p in str(cmyk_value).split(",")]
            if len(parts) != 4:
                raise ValueError("Invalid cmyk")
            c, m, y, k = [float(p) for p in parts]
            if any(v < 0 or v > 100 for v in (c, m, y, k)):
                raise ValueError("Invalid cmyk")
            rgb = _cmyk_to_rgb((c, m, y, k))
            hex_value = _rgb_to_hex(rgb)
        else:
            name_key = str(name_value).strip().lower()
            if name_key not in CSS_COLORS:
                raise ValueError("Unknown color name")
            hex_value = CSS_COLORS[name_key]
            rgb = _hex_to_rgb_tuple(hex_value)
    except (ValueError, TypeError):
        return _error("Invalid color input")

    hsl = _rgb_to_hsl(rgb)
    cmyk = _rgb_to_cmyk(rgb)
    name = None
    for key, hex_code in CSS_COLORS.items():
        if hex_code == hex_value:
            name = key
            break
    ansi16 = _nearest_ansi(rgb)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "hex": f"#{hex_value}",
            "rgb": f"{rgb[0]}, {rgb[1]}, {rgb[2]}",
            "hsl": f"{hsl[0]}, {hsl[1]}, {hsl[2]}",
            "cmyk": f"{cmyk[0]}, {cmyk[1]}, {cmyk[2]}, {cmyk[3]}",
            "ansi16": ansi16,
            "name": name,
            "channels": {
                "rgbChannels": 3,
                "cmykChannels": 4,
                "ansiChannels": 1,
                "hexChannels": 1,
                "hslChannels": 3,
            },
        },
    }
