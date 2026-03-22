from datetime import datetime

import requests


SUN_API = "https://api.sunrise-sunset.org/json"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _parse_duration(seconds: int) -> dict:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return {
        "total_minutes": round(seconds / 60, 2),
        "hours": hours,
        "minutes": minutes,
        "formatted": f"{hours:02d}:{minutes:02d}:00",
    }


def run(params: dict) -> dict:
    params = params or {}
    latitude = params.get("latitude")
    longitude = params.get("longitude")
    date = params.get("date")

    if latitude is None or longitude is None:
        return _error("Missing required parameters: latitude, longitude")

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return _error("Invalid coordinates: latitude and longitude must be numbers")

    query = {"lat": latitude, "lng": longitude, "formatted": 0}
    if date:
        query["date"] = date

    try:
        response = requests.get(SUN_API, params=query, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Daylight service error: {exc}")
    except ValueError:
        return _error("Daylight service returned invalid JSON")

    if payload.get("status") != "OK":
        return _error(payload.get("status", "Daylight service failed"))

    results = payload.get("results") or {}
    sunrise = results.get("sunrise")
    sunset = results.get("sunset")
    if not sunrise or not sunset:
        return _error("Daylight service returned incomplete data")

    try:
        sunrise_dt = datetime.fromisoformat(sunrise.replace("Z", "+00:00"))
        sunset_dt = datetime.fromisoformat(sunset.replace("Z", "+00:00"))
    except ValueError:
        return _error("Daylight service returned invalid timestamps")

    duration_seconds = int((sunset_dt - sunrise_dt).total_seconds())
    date_value = (date or sunrise_dt.date().isoformat())
    day_of_year = datetime.fromisoformat(date_value).timetuple().tm_yday

    data = {
        "date": date_value,
        "location": {"latitude": latitude, "longitude": longitude},
        "condition": "Normal",
        "description": "Standard sunrise and sunset",
        "sunrise": sunrise_dt.strftime("%H:%M:%S"),
        "sunset": sunset_dt.strftime("%H:%M:%S"),
        "daylight_duration": _parse_duration(duration_seconds),
        "day_of_year": day_of_year,
        "is_valid": True,
    }
    return {"status": "ok", "error": None, "data": data}
