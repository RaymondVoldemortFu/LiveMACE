#!/usr/bin/env python3
"""
Create AI trading accounts in batch using API credentials from .env.

Examples:
1) Create preset model accounts:
   python create_accounts_from_env.py

2) Update existing same-name accounts instead of skipping:
   python create_accounts_from_env.py --update-existing

3) Create all model accounts for every API_KEY*/BASE_URL* combination:
   python create_accounts_from_env.py --mode all-combinations

Optional env vars:
- API_KEY / BASE_URL (required in single mode)
- API_KEY_<SUFFIX> / BASE_URL_<SUFFIX> (used by all-combinations mode)
"""

from __future__ import annotations

import argparse
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Tuple

import dotenv

MODEL_LIST = [
    # openai
    "gpt-5.2",
    # deepseek
    "deepseek-v3.2",
    # google
    "gemini-3-pro-preview",
    # xai
    # "grok-420-agents-all", # TODO: waiting for api provider to fix bug in their service
    # qwen
    "qwen3-max",
]

DEFAULT_AGENT_TYPE = "react"
DEFAULT_INITIAL_CAPITAL = Decimal("10000")
DEFAULT_PLACEHOLDER_ACCOUNT_NAME = "GPT"
DEFAULT_CREATE_MODE = "single"


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
    parser.add_argument(
        "--mode",
        choices=[DEFAULT_CREATE_MODE, "all-combinations"],
        default=DEFAULT_CREATE_MODE,
        help=(
            "Account creation mode: "
            "'single' creates one account per model using API_KEY/BASE_URL; "
            "'all-combinations' creates one account per model for each API_KEY*/BASE_URL* pair."
        ),
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


def cleanup_default_gpt_account(db, AccountModel, user_id: int, account_name: str = DEFAULT_PLACEHOLDER_ACCOUNT_NAME) -> Tuple[int, str]:
    """
    Remove default placeholder GPT account if exists for target user.
    Returns (deleted_count, message).
    """
    deleted_count = (
        db.query(AccountModel)
        .filter(AccountModel.user_id == user_id, AccountModel.name == account_name)
        .delete(synchronize_session=False)
    )
    if deleted_count > 0:
        return deleted_count, f"Removed default placeholder account '{account_name}'"
    return 0, f"No default placeholder account named '{account_name}' found"


def _sanitize_env_suffix(raw: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw.strip().lower()).strip("-")
    return normalized or "default"


def _collect_prefixed_env_values(prefix: str) -> Dict[str, str]:
    """
    Collect env values from:
    - PREFIX
    - PREFIX_<SUFFIX>
    """
    collected: Dict[str, str] = {}
    base_value = (os.getenv(prefix) or "").strip()
    if base_value:
        collected["default"] = base_value

    prefix_with_sep = f"{prefix}_"
    for key, value in os.environ.items():
        if not key.startswith(prefix_with_sep):
            continue
        cleaned_value = (value or "").strip()
        if not cleaned_value:
            continue
        suffix = key[len(prefix_with_sep):]
        if not suffix:
            continue
        collected[_sanitize_env_suffix(suffix)] = cleaned_value

    return collected


def build_account_configs(mode: str, default_base_url: str, default_api_key: str) -> List[Tuple[str, str, str]]:
    if mode == DEFAULT_CREATE_MODE:
        return [("default", default_base_url, default_api_key)]

    api_keys = _collect_prefixed_env_values("API_KEY")
    base_urls = _collect_prefixed_env_values("BASE_URL")

    if not api_keys:
        raise SystemExit(
            "Missing API_KEY* values. Please set API_KEY or API_KEY_<SUFFIX> in .env/environment."
        )
    if not base_urls:
        raise SystemExit(
            "Missing BASE_URL* values. Please set BASE_URL or BASE_URL_<SUFFIX> in .env/environment."
        )

    account_configs: List[Tuple[str, str, str]] = []
    for api_key_label in sorted(api_keys):
        for base_url_label in sorted(base_urls):
            combo_name = f"ak-{api_key_label}__bu-{base_url_label}"
            account_configs.append(
                (combo_name, base_urls[base_url_label], api_keys[api_key_label])
            )
    return account_configs


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

    if args.mode == DEFAULT_CREATE_MODE:
        if not api_key:
            raise SystemExit("Missing API_KEY. Please set it in .env or environment variables.")
        if not base_url:
            raise SystemExit("Missing BASE_URL. Please set it in .env or environment variables.")

    account_configs = build_account_configs(
        mode=args.mode,
        default_base_url=base_url,
        default_api_key=api_key,
    )

    created = 0
    updated = 0
    skipped = 0

    db = SessionLocal()
    try:
        user = ensure_user(db, args.user, User)
        deleted_count, cleanup_message = cleanup_default_gpt_account(db, Account, user.id)
        if deleted_count > 0:
            db.commit()
            print(f"[CLEANUP] {cleanup_message}")
        else:
            print(f"[CLEANUP] {cleanup_message}")

        for config_name, config_base_url, config_api_key in account_configs:
            for model in MODEL_LIST:
                name = model if args.mode == DEFAULT_CREATE_MODE else f"{model}__{config_name}"
                existing = (
                    db.query(Account)
                    .filter(Account.user_id == user.id, Account.name == name)
                    .first()
                )
                if existing:
                    if args.update_existing:
                        existing.model = model
                        existing.base_url = config_base_url
                        existing.api_key = config_api_key
                        existing.account_type = "AI"
                        existing.agent_type = DEFAULT_AGENT_TYPE
                        existing.tool_routing_enabled = "true"
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
                    tool_routing_enabled="true",
                    enable_rule_aware="false",
                    is_active="true",
                    model=model,
                    base_url=config_base_url,
                    api_key=config_api_key,
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

    total = len(MODEL_LIST) * len(account_configs)
    print(
        f"Done. created={created}, updated={updated}, skipped={skipped}, total={total}, mode={args.mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
