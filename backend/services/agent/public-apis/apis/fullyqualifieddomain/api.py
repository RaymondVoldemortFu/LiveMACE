import re


DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    domain = params.get("domain")
    if not domain or not isinstance(domain, str):
        return _error("Missing required parameter: domain")

    cleaned = domain.strip().rstrip(".")
    is_fqdn = bool(DOMAIN_RE.match(cleaned))
    data = {"domain": domain, "normalized": cleaned, "fqdn": is_fqdn}
    return {"status": "ok", "error": None, "data": data}
