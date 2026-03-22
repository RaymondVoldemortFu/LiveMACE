#!/usr/bin/env python3
"""
Remove default placeholder GPT account for a target user.

Usage:
  python remove_default_gpt_account.py
  python remove_default_gpt_account.py --user default
  python remove_default_gpt_account.py --name GPT
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove default GPT placeholder account.")
    parser.add_argument(
        "--user",
        default="default",
        help='Target username (default: "default")',
    )
    parser.add_argument(
        "--name",
        default="GPT",
        help='Account name to remove (default: "GPT")',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Ensure sqlite relative path points to backend/data.db
    backend_dir = Path(__file__).resolve().parent
    os.chdir(backend_dir)

    from database.connection import SessionLocal
    from database.models import Account, User
    from create_accounts_from_env import cleanup_default_gpt_account

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == args.user).first()
        if not user:
            print(f"[SKIP] User '{args.user}' not found")
            return 0

        deleted_count, message = cleanup_default_gpt_account(
            db=db,
            AccountModel=Account,
            user_id=user.id,
            account_name=args.name,
        )
        if deleted_count > 0:
            db.commit()
            print(f"[OK] {message} (deleted={deleted_count})")
        else:
            print(f"[OK] {message}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
