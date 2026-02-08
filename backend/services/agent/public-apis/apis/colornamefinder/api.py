import json
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _load_colors() -> dict:
    response = requests.get(CSS_COLOR_URL, timeout=15)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid color list")
    return {name: _parse_hex(hex_value) for name, hex_value in data.items()}


def _distance(a: dict, b: dict) -> float:
    return ((a["r"] - b["r"]) ** 2 + (a["g"] - b["g"]) ** 2 + (a["b"] - b["b"]) ** 2) ** 0.5


def run(params: dict) -> dict:
    params = params or {}
    hex_value = params.get("hex")
    closest = params.get("closest", 1)

    if not hex_value or not isinstance(hex_value, str):
        return _error("Missing required parameter: hex")

    try:
        closest = int(closest)
    except (TypeError, ValueError):
        return _error("Invalid closest: must be an integer between 1 and 20")

    if closest < 1 or closest > 20:
        return _error("Invalid closest: must be between 1 and 20")

    try:
        hex_value = _parse_hex(hex_value)
        rgb = _hex_to_rgb(hex_value)
    except ValueError:
        return _error("Invalid hex: must be a valid hex color")

    try:
        color_map = _load_colors()
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        return _error(f"Color list unavailable: {exc}")

    max_dist = (3 * (255 ** 2)) ** 0.5
    ranked = []
    for name, hex_code in color_map.items():
        c_rgb = _hex_to_rgb(hex_code)
        dist = _distance(rgb, c_rgb)
        similarity = round(100 - (dist / max_dist * 100), 2)
        ranked.append((dist, name, hex_code, c_rgb, similarity))

    ranked.sort(key=lambda item: item[0])
    top = ranked[:closest]
    exact_match = top[0][0] == 0
    closest_color = {
        "name": top[0][1],
        "hex": top[0][2],
        "distance": round(top[0][0], 2),
        "similarity": top[0][4],
        "rgb": top[0][3],
    }
    matches = []
    for dist, name, hex_code, c_rgb, similarity in top:
        matches.append(
            {
                "name": name,
                "hex": hex_code,
                "distance": round(dist, 2),
                "similarity": similarity,
                "rgb": c_rgb,
            }
        )

    return {
        "status": "ok",
        "error": None,
        "data": {
            "input_hex": f"#{hex_value}",
            "input_rgb": rgb,
            "exact_match": exact_match,
            "closest_color": closest_color,
            "closest_matches": matches,
            "total_named_colors": len(color_map),
        },
    }
