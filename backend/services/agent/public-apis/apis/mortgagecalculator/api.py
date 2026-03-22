import math


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    principal = params.get("principal")
    rate = params.get("rate")
    years = params.get("years")
    try:
        principal = float(principal)
        rate = float(rate)
        years = float(years)
    except (TypeError, ValueError):
        return _error("Missing or invalid parameters: principal, rate, years")
    if principal <= 0 or years <= 0:
        return _error("principal and years must be positive")
    monthly_rate = rate / 100 / 12
    n = int(years * 12)
    if abs(monthly_rate) < 1e-10:
        payment = principal / n
    else:
        payment = principal * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1)
    total = payment * n
    data = {
        "principal": principal,
        "annual_rate_percent": rate,
        "years": years,
        "monthly_payment": round(payment, 2),
        "total_payment": round(total, 2),
        "total_interest": round(total - principal, 2),
    }
    return {"status": "ok", "error": None, "data": data}
