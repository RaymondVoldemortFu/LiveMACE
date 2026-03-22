import hashlib
import re
import subprocess
import tempfile
from pathlib import Path


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _run_openssl(args: list, input_path: Path) -> str:
    result = subprocess.run(
        ["openssl", "req", "-in", str(input_path), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "OpenSSL error")
    return result.stdout


def _run_openssl_der(input_path: Path) -> bytes:
    result = subprocess.run(
        ["openssl", "req", "-in", str(input_path), "-outform", "DER"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.decode("utf-8", errors="ignore").strip() or "OpenSSL error")
    return result.stdout


def _format_fingerprint(digest: bytes) -> str:
    return ":".join(f"{b:02X}" for b in digest)


def _parse_subject(subject_line: str) -> dict:
    subject = {
        "common_name": None,
        "organization": None,
        "organizational_unit": None,
        "locality": None,
        "state": None,
        "country": None,
        "email": None,
    }
    cleaned = subject_line.replace("subject=", "").strip()
    pairs = re.split(r",\s*", cleaned)
    for pair in pairs:
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        if key == "CN":
            subject["common_name"] = value
        elif key == "O":
            subject["organization"] = value
        elif key == "OU":
            subject["organizational_unit"] = value
        elif key == "L":
            subject["locality"] = value
        elif key == "ST":
            subject["state"] = value
        elif key == "C":
            subject["country"] = value
        elif key in {"EMAIL", "EMAILADDRESS"}:
            subject["email"] = value
    return subject


def _parse_key_details(text: str) -> dict:
    algorithm = None
    size_bits = None
    exponent = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Public Key Algorithm:"):
            algorithm = line.split(":", 1)[1].strip().upper()
        if "Public-Key:" in line and "(" in line and "bit" in line:
            match = re.search(r"\((\d+)\s+bit\)", line)
            if match:
                size_bits = int(match.group(1))
        if line.startswith("Exponent:"):
            match = re.search(r"Exponent:\s*(\d+)", line)
            if match:
                exponent = int(match.group(1))
    return {"algorithm": algorithm, "size_bits": size_bits, "exponent": exponent}


def _parse_signature_algorithm(text: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("Signature Algorithm:"):
            return line.split(":", 1)[1].strip()
    return None


def run(params: dict) -> dict:
    params = params or {}
    csr = params.get("csr")

    if not csr or not isinstance(csr, str):
        return _error("Missing required parameter: csr")

    if "BEGIN CERTIFICATE REQUEST" not in csr and "BEGIN NEW CERTIFICATE REQUEST" not in csr:
        return _error("Invalid CSR: PEM header missing")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "request.csr"
            path.write_text(csr)

            subject_line = _run_openssl(["-noout", "-subject"], path).strip()
            text_output = _run_openssl(["-noout", "-text"], path)
            der_bytes = _run_openssl_der(path)
    except FileNotFoundError:
        return _error("OpenSSL not available for CSR parsing")
    except ValueError as exc:
        return _error(f"CSR parsing error: {exc}")

    subject = _parse_subject(subject_line)
    public_key = _parse_key_details(text_output)
    signature_algorithm = _parse_signature_algorithm(text_output)

    fingerprints = {
        "sha1": _format_fingerprint(hashlib.sha1(der_bytes).digest()),
        "sha256": _format_fingerprint(hashlib.sha256(der_bytes).digest()),
        "md5": _format_fingerprint(hashlib.md5(der_bytes).digest()),
    }

    data = {
        "version": "v1",
        "subject": subject,
        "public_key": public_key,
        "signature_algorithm": signature_algorithm,
        "extensions": [],
        "fingerprints": fingerprints,
        "pem": csr.strip(),
        "size_bytes": len(der_bytes),
        "format": "PEM",
        "is_valid": True,
    }
    return {"status": "ok", "error": None, "data": data}
