#!/usr/bin/env python3
"""
Create AI trading accounts in batch using API credentials from .env.

Examples:
1) Create preset model accounts:
   python create_accounts_from_env.py

2) Update existing same-name accounts instead of skipping:
   python create_accounts_from_env.py --update-existing

3) Create all model accounts for account config combinations from .env:
   python create_accounts_from_env.py --mode all-combinations

Optional env vars:
- API_KEY / BASE_URL (required)
- ACCOUNT_DEFAULT_* (used by single mode defaults)
- ACCOUNT_COMBO_* (used by all-combinations mode)
"""

from __future__ import annotations

import argparse
import itertools
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
ACCOUNT_FIELD_KEYS = [
    "account_type",
    "agent_type",
    "memory_enabled",
    "tool_routing_enabled",
    "enable_rule_aware",
    "is_active",
]


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
            "'all-combinations' creates one account per model for each ACCOUNT_COMBO_* combination from .env."
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


def _parse_csv_env(env_name: str, default_values: List[str]) -> List[str]:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return list(default_values)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        return list(default_values)
    deduped: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _normalize_bool_text(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return "true"
    if lowered in {"0", "false", "no", "off"}:
        return "false"
    raise SystemExit(
        f"Invalid boolean-like value '{value}'. Please use one of true/false/1/0/yes/no/on/off."
    )


def _build_single_mode_config() -> Dict[str, str]:
    return {
        "account_type": (os.getenv("ACCOUNT_DEFAULT_ACCOUNT_TYPE") or "AI").strip() or "AI",
        "agent_type": (os.getenv("ACCOUNT_DEFAULT_AGENT_TYPE") or DEFAULT_AGENT_TYPE).strip()
        or DEFAULT_AGENT_TYPE,
        "memory_enabled": _normalize_bool_text(
            os.getenv("ACCOUNT_DEFAULT_MEMORY_ENABLED", "false")
        ),
        "tool_routing_enabled": _normalize_bool_text(
            os.getenv("ACCOUNT_DEFAULT_TOOL_ROUTING_ENABLED", "true")
        ),
        "enable_rule_aware": _normalize_bool_text(
            os.getenv("ACCOUNT_DEFAULT_ENABLE_RULE_AWARE", "false")
        ),
        "is_active": _normalize_bool_text(os.getenv("ACCOUNT_DEFAULT_IS_ACTIVE", "true")),
    }


def _build_all_combinations_configs() -> List[Tuple[str, Dict[str, str]]]:
    field_options: Dict[str, List[str]] = {
        "account_type": _parse_csv_env("ACCOUNT_COMBO_ACCOUNT_TYPE", ["AI"]),
        "agent_type": _parse_csv_env("ACCOUNT_COMBO_AGENT_TYPE", [DEFAULT_AGENT_TYPE]),
        "memory_enabled": [
            _normalize_bool_text(v)
            for v in _parse_csv_env("ACCOUNT_COMBO_MEMORY_ENABLED", ["false"])
        ],
        "tool_routing_enabled": [
            _normalize_bool_text(v)
            for v in _parse_csv_env("ACCOUNT_COMBO_TOOL_ROUTING_ENABLED", ["true"])
        ],
        "enable_rule_aware": [
            _normalize_bool_text(v)
            for v in _parse_csv_env("ACCOUNT_COMBO_ENABLE_RULE_AWARE", ["false"])
        ],
        "is_active": [
            _normalize_bool_text(v)
            for v in _parse_csv_env("ACCOUNT_COMBO_IS_ACTIVE", ["true"])
        ],
    }

    combinations: List[Tuple[str, Dict[str, str]]] = []
    for values in itertools.product(*(field_options[key] for key in ACCOUNT_FIELD_KEYS)):
        config = {key: value for key, value in zip(ACCOUNT_FIELD_KEYS, values)}
        combo_name = "__".join(
            [
                f"at-{_sanitize_env_suffix(config['account_type'])}",
                f"ag-{_sanitize_env_suffix(config['agent_type'])}",
                f"mem-{config['memory_enabled']}",
                f"tr-{config['tool_routing_enabled']}",
                f"ra-{config['enable_rule_aware']}",
                f"ia-{config['is_active']}",
            ]
        )
        combinations.append((combo_name, config))
    return combinations


def build_account_configs(mode: str) -> List[Tuple[str, Dict[str, str]]]:
    if mode == DEFAULT_CREATE_MODE:
        return [("default", _build_single_mode_config())]
    return _build_all_combinations_configs()


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

    account_configs = build_account_configs(mode=args.mode)

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

        for config_name, account_config in account_configs:
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
                        existing.base_url = base_url
                        existing.api_key = api_key
                        existing.account_type = account_config["account_type"]
                        existing.agent_type = account_config["agent_type"]
                        existing.memory_enabled = account_config["memory_enabled"]
                        existing.tool_routing_enabled = account_config["tool_routing_enabled"]
                        existing.enable_rule_aware = account_config["enable_rule_aware"]
                        existing.is_active = account_config["is_active"]
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
                    account_type=account_config["account_type"],
                    agent_type=account_config["agent_type"],
                    memory_enabled=account_config["memory_enabled"],
                    tool_routing_enabled=account_config["tool_routing_enabled"],
                    enable_rule_aware=account_config["enable_rule_aware"],
                    is_active=account_config["is_active"],
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

    total = len(MODEL_LIST) * len(account_configs)
    print(
        f"Done. created={created}, updated={updated}, skipped={skipped}, total={total}, mode={args.mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
