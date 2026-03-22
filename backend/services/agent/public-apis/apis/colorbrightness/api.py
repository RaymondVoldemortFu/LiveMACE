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


def _relative_luminance(rgb: dict) -> float:
    def _channel(c: int) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r = _channel(rgb["r"])
    g = _channel(rgb["g"])
    b = _channel(rgb["b"])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(l1: float, l2: float) -> float:
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def run(params: dict) -> dict:
    params = params or {}
    hex_value = params.get("hex")
    if not hex_value or not isinstance(hex_value, str):
        return _error("Missing required parameter: hex")

    try:
        hex_value = _parse_hex(hex_value)
    except ValueError:
        return _error("Invalid hex: must be a valid hex color")

    rgb = _hex_to_rgb(hex_value)
    luminance = _relative_luminance(rgb)
    perceived = round(0.299 * rgb["r"] + 0.587 * rgb["g"] + 0.114 * rgb["b"])
    yiq = round((rgb["r"] * 299 + rgb["g"] * 587 + rgb["b"] * 114) / 1000, 2)
    is_light = luminance > 0.5
    is_dark = not is_light
    brightness_category = "light" if is_light else "dark"

    contrast_with_white = _contrast_ratio(luminance, 1.0)
    contrast_with_black = _contrast_ratio(luminance, 0.0)
    recommended_text_color = "#FFFFFF" if contrast_with_white >= contrast_with_black else "#000000"

    wcag_aa_white = contrast_with_white >= 4.5
    wcag_aa_black = contrast_with_black >= 4.5
    wcag_aaa_white = contrast_with_white >= 7.0
    wcag_aaa_black = contrast_with_black >= 7.0

    return {
        "status": "ok",
        "error": None,
        "data": {
            "hex": f"#{hex_value}",
            "rgb": rgb,
            "luminance": round(luminance, 4),
            "perceived_brightness": perceived,
            "yiq": yiq,
            "is_light": is_light,
            "is_dark": is_dark,
            "brightness_category": brightness_category,
            "recommended_text_color": recommended_text_color,
            "contrast_ratio_with_white": contrast_with_white,
            "contrast_ratio_with_black": contrast_with_black,
            "wcag_aa_compliant_with_white": wcag_aa_white,
            "wcag_aa_compliant_with_black": wcag_aa_black,
            "wcag_aaa_compliant_with_white": wcag_aaa_white,
            "wcag_aaa_compliant_with_black": wcag_aaa_black,
        },
    }
