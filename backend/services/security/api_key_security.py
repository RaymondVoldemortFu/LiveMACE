import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from config.settings import SECURITY_SETTINGS


ENC_PREFIX = "enc$v1$"
LEGACY_HASH_PREFIX = "sha256$"
DEFAULT_API_KEYS = {
    "default-key-please-update-in-settings",
    "default",
}


def _looks_like_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def is_hashed_api_key(value: Optional[str]) -> bool:
    if not value or not isinstance(value, str):
        return False
    if not value.startswith(LEGACY_HASH_PREFIX):
        return False
    digest = value[len(LEGACY_HASH_PREFIX):]
    return _looks_like_sha256_hex(digest)


def is_encrypted_api_key(value: Optional[str]) -> bool:
    if not value or not isinstance(value, str):
        return False
    return value.startswith(ENC_PREFIX)


def _get_cipher() -> Optional[Fernet]:
    secret = SECURITY_SETTINGS.api_key_cipher_key
    if not secret:
        return None
    return Fernet(secret.encode("utf-8"))


def encrypt_api_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if is_encrypted_api_key(raw):
        return raw

    cipher = _get_cipher()
    if cipher is None:
        raise ValueError("API_KEY_CIPHER_KEY is not configured; cannot encrypt api_key")

    token = cipher.encrypt(raw.encode("utf-8")).decode("utf-8")
    return f"{ENC_PREFIX}{token}"


def mask_api_key_for_display(value: Optional[str]) -> str:
    if not value:
        return ""
    if is_encrypted_api_key(value):
        return "ENC(****)"
    if is_hashed_api_key(value):
        return f"HASH({value[len(LEGACY_HASH_PREFIX):][:8]}...)"
    return "****" + value[-4:] if len(value) >= 4 else "****"


def is_default_api_key(value: Optional[str]) -> bool:
    if value is None:
        return True
    raw = str(value).strip()
    return (not raw) or (raw in DEFAULT_API_KEYS)


def resolve_runtime_api_key(value: Optional[str], fallback_env_var: Optional[str] = None) -> Optional[str]:
    """
    For encrypted values stored in DB, runtime decrypts with API_KEY_CIPHER_KEY.
    For legacy hashed values, runtime uses fallback env key.
    """
    fallback_var = (
        (fallback_env_var or "").strip() or SECURITY_SETTINGS.api_key_fallback_env_var or "API_KEY"
    )

    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if is_encrypted_api_key(raw):
        cipher = _get_cipher()
        if cipher is None:
            fallback = (os.getenv(fallback_var) or "").strip()
            return fallback or None
        token = raw[len(ENC_PREFIX):]
        try:
            return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            fallback = (os.getenv(fallback_var) or "").strip()
            return fallback or None
    if is_hashed_api_key(raw):
        fallback = (os.getenv(fallback_var) or "").strip()
        return fallback or None
    return raw
