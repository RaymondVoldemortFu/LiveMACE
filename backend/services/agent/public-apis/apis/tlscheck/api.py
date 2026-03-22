import ssl
import socket


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
                version = ssock.version()
                cert = ssock.getpeercert()
                not_after = cert.get("notAfter", "")
                cipher = ssock.cipher()
        data = {"host": host, "port": port, "tls_version": version, "valid": True, "not_after": not_after, "cipher": cipher[0] if cipher else None}
        return {"status": "ok", "error": None, "data": data}
    except ssl.SSLError as e:
        data = {"host": host, "port": port, "valid": False, "error": str(e)}
        return {"status": "ok", "error": None, "data": data}
    except (socket.gaierror, socket.timeout, OSError) as e:
        return _error(str(e))
