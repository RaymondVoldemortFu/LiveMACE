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
        return _error("Missing required parameters: principal, rate, years")

    monthly_rate = rate / 100 / 12
    n = int(years * 12)
    if monthly_rate == 0:
        payment = principal / n
    else:
        payment = (principal * monthly_rate) / (1 - (1 + monthly_rate) ** -n)
    total = payment * n

    data = {"monthly_payment": payment, "total_payment": total, "total_interest": total - principal}
    return {"status": "ok", "error": None, "data": data}
