import json
from functools import lru_cache

import colorsys
import requests


CSS_COLOR_URL = "https://raw.githubusercontent.com/bahamas10/css-color-names/master/css-color-names.json"


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


def _rgb_to_hex(rgb: dict) -> str:
    return "{:02X}{:02X}{:02X}".format(rgb["r"], rgb["g"], rgb["b"])


def _rgb_to_hsl(rgb: dict) -> dict:
    r, g, b = [rgb[c] / 255.0 for c in ("r", "g", "b")]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return {"h": round(h * 360, 1), "s": round(s * 100, 1), "l": round(l * 100, 1)}


def _hsl_to_rgb(hsl: dict) -> dict:
    r, g, b = colorsys.hls_to_rgb(hsl["h"] / 360.0, hsl["l"] / 100.0, hsl["s"] / 100.0)
    return {"r": round(r * 255), "g": round(g * 255), "b": round(b * 255)}


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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@lru_cache(maxsize=1)
def _load_colors() -> dict:
    response = requests.get(CSS_COLOR_URL, timeout=15)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid color list")
    return {name: _parse_hex(hex_value) for name, hex_value in data.items()}


def _nearest_name(rgb: dict, colors: dict) -> str:
    best_name = None
    best_dist = None
    for name, hex_value in colors.items():
        c_rgb = _hex_to_rgb(hex_value)
        dist = (rgb["r"] - c_rgb["r"]) ** 2 + (rgb["g"] - c_rgb["g"]) ** 2 + (rgb["b"] - c_rgb["b"]) ** 2
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _apply_variation(hsl: dict, variation: str) -> dict:
    s = hsl["s"]
    l = hsl["l"]
    if variation == "soft":
        s *= 0.7
        l *= 1.1
    elif variation == "pastel":
        s *= 0.6
        l *= 1.2
    elif variation == "light":
        l *= 1.2
    elif variation == "hard":
        s *= 1.1
        l *= 0.9
    elif variation == "pale":
        s *= 0.4
        l *= 1.3
    return {"h": hsl["h"], "s": _clamp(s, 0, 100), "l": _clamp(l, 0, 100)}


def _web_safe_channel(value: int) -> int:
    steps = [0, 51, 102, 153, 204, 255]
    return min(steps, key=lambda x: abs(x - value))


def _web_safe(rgb: dict) -> dict:
    return {
        "r": _web_safe_channel(rgb["r"]),
        "g": _web_safe_channel(rgb["g"]),
        "b": _web_safe_channel(rgb["b"]),
    }


def run(params: dict) -> dict:
    params = params or {}
    color = params.get("color")
    scheme = str(params.get("scheme", "triade")).lower()
    variation = str(params.get("variation", "default")).lower()
    count = params.get("count", 5)
    distance = params.get("distance", 0.5)
    web_safe = bool(params.get("webSafe", False))

    if not color or not isinstance(color, str):
        return _error("Missing required parameter: color")

    try:
        count = int(count)
    except (TypeError, ValueError):
        return _error("Invalid count: must be an integer between 1 and 16")

    if count < 1 or count > 16:
        return _error("Invalid count: must be between 1 and 16")

    try:
        distance = float(distance)
    except (TypeError, ValueError):
        return _error("Invalid distance: must be a number between 0 and 1")

    if distance < 0 or distance > 1:
        return _error("Invalid distance: must be between 0 and 1")

    try:
        base_hex = _parse_hex(color)
    except ValueError:
        return _error("Invalid color: must be a valid hex string")

    try:
        color_map = _load_colors()
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        return _error(f"Color list unavailable: {exc}")

    base_rgb = _hex_to_rgb(base_hex)
    base_hsl = _rgb_to_hsl(base_rgb)

    if scheme not in {"mono", "contrast", "triade", "tetrade", "analogic"}:
        return _error("Invalid scheme: must be mono, contrast, triade, tetrade, or analogic")

    if variation not in {"default", "soft", "pastel", "light", "hard", "pale"}:
        return _error("Invalid variation: must be default, soft, pastel, light, hard, or pale")

    hue = base_hsl["h"]
    if scheme == "mono":
        hues = [hue] * count
    elif scheme == "contrast":
        hues = [hue, (hue + 180) % 360]
    elif scheme == "triade":
        hues = [hue, (hue + 120) % 360, (hue + 240) % 360]
    elif scheme == "tetrade":
        hues = [hue, (hue + 90) % 360, (hue + 180) % 360, (hue + 270) % 360]
    else:
        offset = 30 * distance if distance > 0 else 30
        hues = [(hue - offset) % 360, hue, (hue + offset) % 360]

    palette = []
    for idx in range(count):
        h = hues[idx % len(hues)]
        hsl = {"h": h, "s": base_hsl["s"], "l": base_hsl["l"]}
        hsl = _apply_variation(hsl, variation)
        rgb = _hsl_to_rgb(hsl)
        if web_safe:
            rgb = _web_safe(rgb)
        hex_code = _rgb_to_hex(rgb)
        name = _nearest_name(rgb, color_map)
        luminance = round(_relative_luminance(rgb), 3)
        is_dark = luminance < 0.5
        contrast_white = _contrast_ratio(luminance, 1.0)
        contrast_black = _contrast_ratio(luminance, 0.0)
        text_color = "#FFFFFF" if contrast_white >= contrast_black else "#000000"
        palette.append(
            {
                "hex": f"#{hex_code}",
                "name": name,
                "rgb": rgb,
                "hsl": hsl,
                "luminance": luminance,
                "isDark": is_dark,
                "textColor": text_color,
                "accessibility": {
                    "contrastWithWhite": contrast_white,
                    "contrastWithBlack": contrast_black,
                    "wcagAANormal": contrast_white >= 4.5 or contrast_black >= 4.5,
                    "wcagAALarge": contrast_white >= 3.0 or contrast_black >= 3.0,
                    "wcagAAA": contrast_white >= 7.0 or contrast_black >= 7.0,
                },
            }
        )

    raw = [item["hex"].lstrip("#").lower() for item in palette]
    labels = ["primary", "secondary", "tertiary", "quaternary", "quinary"]
    css_lines = [":root {"]
    for idx, item in enumerate(palette):
        name = labels[idx] if idx < len(labels) else f"color-{idx + 1}"
        css_lines.append(f" --{name}: {item['hex']};")
        css_lines.append(f" --{name}-rgb: {item['rgb']['r']}, {item['rgb']['g']}, {item['rgb']['b']};")
    css_lines.append("}")

    return {
        "status": "ok",
        "error": None,
        "data": {
            "source": f"#{base_hex}",
            "sourceName": _nearest_name(base_rgb, color_map),
            "hue": round(base_hsl["h"]),
            "scheme": scheme,
            "variation": variation,
            "distance": round(distance, 2),
            "colorCount": len(palette),
            "colorPalette": palette,
            "colorPaletteRaw": raw,
            "css": "\n".join(css_lines),
            "image": None,
        },
    }
