from typing import Tuple


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _classify_bmi(bmi: float) -> Tuple[str, str, str]:
    if bmi < 18.5:
        return (
            "Low risk",
            "This weight is under the normal range.",
            "A BMI below 18.5 falls in the underweight range. Consider consulting a healthcare professional "
            "to assess nutritional status and overall health.",
        )
    if bmi < 25:
        return (
            "Low risk",
            "This weight is normal and you are healthy.",
            "A BMI between 18.5 and 24.9 falls within the normal weight range according to the World Health "
            "Organization. This range is associated with the lowest health risk for many conditions.",
        )
    if bmi < 30:
        return (
            "Medium risk",
            "This weight is above the normal range.",
            "A BMI between 25 and 29.9 is classified as overweight. Consider a balanced diet and regular "
            "physical activity to reduce health risks.",
        )
    return (
        "High risk",
        "This weight is in the obese range.",
        "A BMI of 30 or higher is classified as obese, which is associated with increased risk for several "
        "health conditions. Consider consulting a healthcare professional for guidance.",
    )


def run(params: dict) -> dict:
    params = params or {}
    weight = params.get("weight")
    height = params.get("height")
    unit = params.get("unit")

    if weight is None or height is None or not unit:
        return _error("Missing required parameter: weight, height, unit")

    try:
        weight = float(weight)
        height = float(height)
    except (TypeError, ValueError):
        return _error("Invalid weight or height: must be numeric")

    unit = str(unit).lower().strip()
    if unit not in {"metric", "imperial"}:
        return _error("Invalid unit: must be metric or imperial")

    if weight <= 0 or height <= 0:
        return _error("Invalid weight or height: must be positive values")

    if unit == "imperial":
        weight_kg = weight * 0.45359237
        height_cm = height * 30.48
        weight_label = f"{weight} lb"
        height_label = f"{height} ft"
    else:
        weight_kg = weight
        height_cm = height
        weight_label = f"{weight} kg"
        height_label = f"{height} cm"

    height_m = height_cm / 100.0
    bmi = weight_kg / (height_m ** 2)
    risk, summary, recommendation = _classify_bmi(bmi)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "height": height_label,
            "weight": weight_label,
            "bmi": bmi,
            "risk": risk,
            "summary": summary,
            "recommendation": recommendation,
        },
    }
