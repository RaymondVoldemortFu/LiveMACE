import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    tax_id = params.get("tax_id") or params.get("tin") or params.get("ssn") or params.get("id")
    country = (params.get("country") or "US").strip().upper()
    if tax_id is None or str(tax_id).strip() == "":
        return _error("Missing required parameter: tax_id or tin")
    s = re.sub(r"[\s\-]", "", str(tax_id).strip())
    if country == "US":
        if len(s) != 9 or not s.isdigit():
            data = {"valid": False, "tax_id": tax_id, "country": country, "reason": "US SSN/TIN must be 9 digits"}
        else:
            data = {"valid": True, "tax_id": tax_id, "formatted": f"{s[:3]}-{s[3:5]}-{s[5:]}", "country": country}
    else:
        data = {"valid": bool(s.isalnum() and 5 <= len(s) <= 20), "tax_id": tax_id, "country": country}
    return {"status": "ok", "error": None, "data": data}
