import ssl
import socket
from datetime import datetime, timezone


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    host = params.get("host") or params.get("domain") or params.get("url")
    if not host or not str(host).strip():
        return _error("Missing required parameter: host or domain")
    host = str(host).strip()
    if "://" in host:
        host = host.split("://", 1)[1].split("/", 1)[0]
    port = 443
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                not_before = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                valid = not_after > datetime.now(timezone.utc)
                issuer = dict(x[0] for x in cert.get("issuer", []))
                subject = dict(x[0] for x in cert.get("subject", []))
        data = {
            "host": host,
            "port": port,
            "valid": valid,
            "issuer": issuer.get("organizationName") or str(issuer),
            "subject": subject.get("commonName") or str(subject),
            "not_before": cert["notBefore"],
            "not_after": cert["notAfter"],
        }
        return {"status": "ok", "error": None, "data": data}
    except ssl.SSLError as e:
        return _error(f"SSL error: {e}")
    except (socket.gaierror, socket.timeout, OSError) as e:
        return _error(str(e))
