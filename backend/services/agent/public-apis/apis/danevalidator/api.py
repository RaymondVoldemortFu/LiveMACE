import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


USAGE_MAP = {
    0: ("PKIX-TA", "CA constraint"),
    1: ("PKIX-EE", "Service certificate constraint"),
    2: ("DANE-TA", "Trust anchor assertion"),
    3: ("DANE-EE", "Domain-issued certificate"),
}
SELECTOR_MAP = {
    0: ("Full Certificate", "Full certificate"),
    1: ("SPKI", "SubjectPublicKeyInfo"),
}
MATCHING_MAP = {
    0: ("Full", "No hash"),
    1: ("SHA-256", "SHA-256 hash"),
    2: ("SHA-512", "SHA-512 hash"),
}


def _is_hex(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Fa-f]+", value or ""))


def _parse_record(record: str) -> dict:
    tokens = record.strip().split()
    if "TLSA" not in tokens:
        raise ValueError("Record does not contain TLSA")
    idx = tokens.index("TLSA")
    if idx + 4 >= len(tokens):
        raise ValueError("Incomplete TLSA record")

    name = tokens[0]
    ttl = None
    dns_class = None
    if idx >= 3:
        if tokens[1].isdigit():
            ttl = int(tokens[1])
            dns_class = tokens[2]
        else:
            dns_class = tokens[1]

    usage = int(tokens[idx + 1])
    selector = int(tokens[idx + 2])
    matching = int(tokens[idx + 3])
    certificate_data = tokens[idx + 4]

    port = None
    protocol = None
    hostname = None
    if name.startswith("_"):
        parts = name.split(".")
        if len(parts) >= 3:
            try:
                port = int(parts[0].lstrip("_"))
            except ValueError:
                port = None
            protocol = parts[1].lstrip("_")
            hostname = ".".join(parts[2:]).rstrip(".")

    return {
        "name": name,
        "port": port,
        "protocol": protocol,
        "hostname": hostname,
        "ttl": ttl,
        "class": dns_class,
        "usage": usage,
        "selector": selector,
        "matching": matching,
        "certificate_data": certificate_data,
        "certificate_data_length": len(certificate_data),
    }


def run(params: dict) -> dict:
    params = params or {}
    record = params.get("record")

    if not record or not isinstance(record, str):
        return _error("Missing required parameter: record")
    try:
        parsed = _parse_record(record)
    except (ValueError, TypeError) as exc:
        return _error(f"Invalid DANE record: {exc}")

    usage_info = USAGE_MAP.get(parsed["usage"], ("Unknown", "Unknown usage"))
    selector_info = SELECTOR_MAP.get(parsed["selector"], ("Unknown", "Unknown selector"))
    matching_info = MATCHING_MAP.get(parsed["matching"], ("Unknown", "Unknown matching"))

    hex_valid = _is_hex(parsed["certificate_data"])
    length_valid = parsed["certificate_data_length"] % 2 == 0
    recommended = parsed["usage"] == 3 and parsed["selector"] == 1 and parsed["matching"] == 1

    data = {
        "raw_record": record,
        "parsed": parsed,
        "interpretation": {
            "usage": {
                "name": usage_info[0],
                "description": usage_info[1],
                "full_description": usage_info[1],
            },
            "selector": {
                "name": selector_info[0],
                "description": selector_info[1],
                "full_description": selector_info[1],
            },
            "matching": {
                "name": matching_info[0],
                "description": matching_info[1],
                "full_description": matching_info[1],
            },
            "security_level": "Recommended" if recommended else "Unknown",
            "recommendation": (
                "This is the recommended DANE configuration (DANE-EE + SPKI + SHA-256)"
                if recommended
                else "No recommendation"
            ),
        },
        "validation": {
            "is_valid": hex_valid and length_valid,
            "certificate_data_format": "Valid hexadecimal" if hex_valid else "Invalid hexadecimal",
            "certificate_data_length_valid": length_valid,
        },
    }
    return {"status": "ok", "error": None, "data": data}
