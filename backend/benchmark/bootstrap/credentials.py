"""Credential bootstrap stage (M18): placeholder cleanup and key migration.

Logic moved verbatim from ``main.py``'s ``on_startup``:

- accounts still holding the seeded placeholder api_key are wiped so the
  system never treats fake OpenAI defaults as real config;
- legacy plaintext api_keys are migrated to encrypted-at-rest storage.

Both steps are idempotent and report how many rows they touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PLACEHOLDER_API_KEY = "default-key-please-update-in-settings"


@dataclass
class CredentialReport:
    placeholders_cleared: int = 0
    plaintext_keys_encrypted: int = 0


def clear_placeholder_credentials(db: Session) -> int:
    from database.models import Account

    placeholder_accounts = (
        db.query(Account).filter(Account.api_key == PLACEHOLDER_API_KEY).all()
    )
    for account in placeholder_accounts:
        account.model = None
        account.base_url = None
        account.api_key = None
    if placeholder_accounts:
        db.commit()
    return len(placeholder_accounts)


def migrate_legacy_plaintext_api_keys(db: Session) -> int:
    from database.models import Account
    from services.security.api_key_security import (
        encrypt_api_key,
        is_default_api_key,
        is_encrypted_api_key,
        is_hashed_api_key,
    )

    migrated_count = 0
    for account in db.query(Account).all():
        key = account.api_key
        if not key or is_default_api_key(key) or is_hashed_api_key(key) or is_encrypted_api_key(key):
            continue
        account.api_key = encrypt_api_key(key)
        migrated_count += 1
    if migrated_count:
        db.commit()
    return migrated_count


def run_credential_bootstrap(
    session_factory: Optional[Callable[[], Session]] = None,
) -> CredentialReport:
    if session_factory is None:
        from database.connection import SessionLocal as session_factory

    report = CredentialReport()
    db = session_factory()
    try:
        report.placeholders_cleared = clear_placeholder_credentials(db)
        report.plaintext_keys_encrypted = migrate_legacy_plaintext_api_keys(db)
    finally:
        db.close()
    logger.info(
        "credential bootstrap: placeholders_cleared=%s plaintext_keys_encrypted=%s",
        report.placeholders_cleared,
        report.plaintext_keys_encrypted,
    )
    return report
