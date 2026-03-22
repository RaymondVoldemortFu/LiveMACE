import colorsys


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_hex(value: str) -> str:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError("Invalid hex color")
    return value.upper()


def _hex_to_rgb(value: str) -> dict:
    return {
        "r": int(value[0:2], 16),
        "g": int(value[2:4], 16),
        "b": int(value[4:6], 16),
    }


def _rgb_to_hsl(rgb: dict) -> dict:
    r, g, b = [rgb[c] / 255.0 for c in ("r", "g", "b")]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return {"h": round(h * 360, 2), "s": round(s * 100, 2), "l": round(l * 100, 2)}


def run(params: dict) -> dict:
    params = params or {}
    color1 = params.get("color1")
    color2 = params.get("color2")

    if not color1 or not color2:
        return _error("Missing required parameters: color1, color2")

    try:
        color1 = _parse_hex(color1)
        color2 = _parse_hex(color2)
    except ValueError:
        return _error("Invalid color: must be valid hex strings")

    rgb1 = _hex_to_rgb(color1)
    rgb2 = _hex_to_rgb(color2)
    hsl1 = _rgb_to_hsl(rgb1)
    hsl2 = _rgb_to_hsl(rgb2)

    rgb_distance = ((rgb1["r"] - rgb2["r"]) ** 2 + (rgb1["g"] - rgb2["g"]) ** 2 + (rgb1["b"] - rgb2["b"]) ** 2) ** 0.5
    max_dist = (3 * (255 ** 2)) ** 0.5
    rgb_similarity = round(100 - (rgb_distance / max_dist * 100), 2)

    hue_diff = abs(hsl1["h"] - hsl2["h"])
    hue_diff = min(hue_diff, 360 - hue_diff)
    sat_diff = abs(hsl1["s"] - hsl2["s"])
    light_diff = abs(hsl1["l"] - hsl2["l"])
    hsl_similarity = round(100 - ((hue_diff / 180) * 40 + (sat_diff / 100) * 30 + (light_diff / 100) * 30), 2)
    overall_similarity = round((rgb_similarity + hsl_similarity) / 2, 2)

    if overall_similarity >= 95:
        category = "nearly identical"
    elif overall_similarity >= 80:
        category = "very similar"
    elif overall_similarity >= 60:
        category = "similar"
    elif overall_similarity >= 40:
        category = "somewhat similar"
    else:
        category = "different"

    return {
        "status": "ok",
        "error": None,
        "data": {
            "color1": {"hex": f"#{color1}", "rgb": rgb1, "hsl": hsl1},
            "color2": {"hex": f"#{color2}", "rgb": rgb2, "hsl": hsl2},
            "rgb_distance": round(rgb_distance, 2),
            "rgb_similarity": rgb_similarity,
            "hsl_similarity": hsl_similarity,
            "overall_similarity": overall_similarity,
            "delta_e": round(rgb_distance, 2),
            "hue_difference": round(hue_diff, 2),
            "saturation_difference": round(sat_diff, 2),
            "lightness_difference": round(light_diff, 2),
            "similarity_category": category,
            "are_identical": rgb_distance == 0,
        },
    }
