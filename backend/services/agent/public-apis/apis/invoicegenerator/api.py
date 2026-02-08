import datetime as _dt
import uuid


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    items = params.get("items") or []
    if not isinstance(items, list):
        return _error("Invalid items: must be a list")

    total = 0.0
    normalized_items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = item.get("quantity", 1)
        price = item.get("price", 0)
        try:
            qty = float(qty)
            price = float(price)
        except (TypeError, ValueError):
            qty = 1.0
            price = 0.0
        line_total = qty * price
        total += line_total
        normalized_items.append(
            {
                "description": item.get("description"),
                "quantity": qty,
                "price": price,
                "total": line_total,
            }
        )

    invoice = {
        "invoice_id": params.get("invoice_id") or str(uuid.uuid4()),
        "date": params.get("date") or _dt.date.today().isoformat(),
        "from": params.get("from"),
        "to": params.get("to"),
        "currency": params.get("currency") or "USD",
        "items": normalized_items,
        "subtotal": total,
        "tax": params.get("tax") or 0,
        "total": total + float(params.get("tax") or 0),
        "notes": params.get("notes"),
    }
    return {"status": "ok", "error": None, "data": invoice}
