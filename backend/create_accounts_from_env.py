#!/usr/bin/env python3
"""
Create AI trading accounts in batch using API credentials from .env.

Examples:
1) Create preset model accounts:
   python create_accounts_from_env.py

2) Update existing same-name accounts instead of skipping:
   python create_accounts_from_env.py --update-existing

Optional env vars:
- API_KEY (required)
- BASE_URL (required)
"""

from __future__ import annotations

import argparse
import os
from decimal import Decimal
from pathlib import Path

import dotenv

MODEL_LIST = [
    # openai
    "gpt-5.2",
    # deepseek
    "deepseek-v3.2",
    # google
    "gemini-3-pro-preview",
    # anthropic
    "claude-opus-4-6",
    # qwen
    "qwen3-max",
]

DEFAULT_AGENT_TYPE = "advanced_multi_agent"
DEFAULT_INITIAL_CAPITAL = Decimal("10000")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create preset model accounts using API_KEY/BASE_URL from .env"
    )
    parser.add_argument(
        "--user",
        default="default",
        help='Target username to attach accounts to (default: "default")',
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="If account name already exists, update model/base_url/api_key instead of skipping",
    )
    return parser.parse_args()


def ensure_user(db, username: str, UserModel):
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if user:
        return user
    user = UserModel(username=username, email=None, password_hash=None, is_active="true")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def main() -> int:
    args = parse_args()

    # Ensure sqlite relative path always points to backend/data.db.
    backend_dir = Path(__file__).resolve().parent
    os.chdir(backend_dir)

    from database.connection import SessionLocal
    from database.models import Account, User

    # Load .env from current/parent dirs but do not override process env.
    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True), override=False)
    api_key = (os.getenv("API_KEY") or "").strip()
    base_url = (os.getenv("BASE_URL") or "").strip()

    if not api_key:
        raise SystemExit("Missing API_KEY. Please set it in .env or environment variables.")
    if not base_url:
        raise SystemExit("Missing BASE_URL. Please set it in .env or environment variables.")

    created = 0
    updated = 0
    skipped = 0

    db = SessionLocal()
    try:
        user = ensure_user(db, args.user, User)
        for model in MODEL_LIST:
            name = model
            existing = (
                db.query(Account)
                .filter(Account.user_id == user.id, Account.name == name)
                .first()
            )
            if existing:
                if args.update_existing:
                    existing.model = model
                    existing.base_url = base_url
                    existing.api_key = api_key
                    existing.account_type = "AI"
                    existing.agent_type = DEFAULT_AGENT_TYPE
                    existing.is_active = "true"
                    updated += 1
                    print(f"[UPDATED] {name} (model={model})")
                else:
                    skipped += 1
                    print(f"[SKIPPED] {name} already exists")
                continue

            account = Account(
                user_id=user.id,
                version="v1",
                name=name,
                account_type="AI",
                agent_type=DEFAULT_AGENT_TYPE,
                enable_rule_aware="false",
                is_active="true",
                model=model,
                base_url=base_url,
                api_key=api_key,
                initial_capital=DEFAULT_INITIAL_CAPITAL,
                current_cash=DEFAULT_INITIAL_CAPITAL,
                frozen_cash=Decimal("0"),
            )
            db.add(account)
            created += 1
            print(f"[CREATED] {name} (model={model})")

        db.commit()
    finally:
        db.close()

    print(
        f"Done. created={created}, updated={updated}, skipped={skipped}, total={len(MODEL_LIST)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
