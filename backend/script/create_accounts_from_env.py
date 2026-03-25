#!/usr/bin/env python3
"""
Create AI trading accounts in batch using API credentials from .env.

Examples:
1) Create preset model accounts:
   python script/create_accounts_from_env.py

2) Update existing same-name accounts instead of skipping:
   python script/create_accounts_from_env.py --update-existing

3) Create all model accounts for account config combinations from .env:
   python script/create_accounts_from_env.py --mode all-combinations

Optional env vars:
- API_KEY / BASE_URL (required)
- ACCOUNT_DEFAULT_* (used by single mode defaults)
- ACCOUNT_COMBO_CSV_PATH (used by all-combinations mode)
"""

from __future__ import annotations

import argparse
import csv
import os
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
            "'all-combinations' creates one account per model for each row in ACCOUNT_COMBO_CSV_PATH."
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


def _normalize_account_config(config: Dict[str, str]) -> Dict[str, str]:
    normalized = dict(config)
    normalized["account_type"] = (normalized.get("account_type") or "").strip() or "AI"
    normalized["agent_type"] = (
        (normalized.get("agent_type") or "").strip() or DEFAULT_AGENT_TYPE
    )
    normalized["memory_enabled"] = _normalize_bool_text(normalized.get("memory_enabled", "false"))
    normalized["tool_routing_enabled"] = _normalize_bool_text(
        normalized.get("tool_routing_enabled", "true")
    )
    normalized["enable_rule_aware"] = _normalize_bool_text(
        normalized.get("enable_rule_aware", "false")
    )
    normalized["is_active"] = _normalize_bool_text(normalized.get("is_active", "true"))
    return normalized


def _resolve_combo_csv_path() -> Path:
    raw_path = (os.getenv("ACCOUNT_COMBO_CSV_PATH") or "").strip()
    if not raw_path:
        raise SystemExit(
            "Missing ACCOUNT_COMBO_CSV_PATH. Please set it in .env when using --mode all-combinations."
        )

    csv_path = Path(raw_path).expanduser()
    if not csv_path.is_absolute():
        csv_path = (Path.cwd() / csv_path).resolve()
    if not csv_path.exists():
        raise SystemExit(f"ACCOUNT_COMBO_CSV_PATH not found: {csv_path}")
    if not csv_path.is_file():
        raise SystemExit(f"ACCOUNT_COMBO_CSV_PATH is not a file: {csv_path}")
    return csv_path


def _build_all_combinations_configs() -> List[Tuple[str, Dict[str, str]]]:
    csv_path = _resolve_combo_csv_path()
    combinations: List[Tuple[str, Dict[str, str]]] = []

    with csv_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise SystemExit(
                f"CSV file has no header: {csv_path}. "
                f"Expected columns: {', '.join(ACCOUNT_FIELD_KEYS)}"
            )

        header_columns = {h.strip() for h in reader.fieldnames if h}
        missing_columns = [col for col in ACCOUNT_FIELD_KEYS if col not in header_columns]
        if missing_columns:
            raise SystemExit(
                f"CSV missing required columns: {', '.join(missing_columns)}. "
                f"File: {csv_path}"
            )

        for row_num, row in enumerate(reader, start=2):
            if not row:
                continue
            raw_values = {k: (row.get(k) or "").strip() for k in ACCOUNT_FIELD_KEYS}
            if not any(raw_values.values()):
                continue
            config = _normalize_account_config(raw_values)
            combinations.append((f"csv-row-{row_num - 1:03d}", config))

    if not combinations:
        raise SystemExit(f"No valid combination rows found in CSV: {csv_path}")
    return combinations


def build_account_configs(mode: str) -> List[Tuple[str, Dict[str, str]]]:
    if mode == DEFAULT_CREATE_MODE:
        return [("default", _build_single_mode_config())]
    return _build_all_combinations_configs()


def build_account_name(model: str, account_config: Dict[str, str]) -> str:
    """
    Naming rule:
    - base: <model>-<agent_type>
    - suffix: append -tool / -memory / -rule when enabled
    """
    base = f"{(model or '').strip()}-{(account_config.get('agent_type') or DEFAULT_AGENT_TYPE).strip()}"
    suffixes: List[str] = []
    if account_config.get("tool_routing_enabled") == "true":
        suffixes.append("tool")
    if account_config.get("memory_enabled") == "true":
        suffixes.append("memory")
    if account_config.get("enable_rule_aware") == "true":
        suffixes.append("rule")
    return "-".join([base, *suffixes]) if suffixes else base


def validate_unique_account_names(
    account_configs: List[Tuple[str, Dict[str, str]]], models: List[str]
) -> None:
    """
    Guard against naming collisions in all-combinations mode.
    With the new naming rule, combinations that only differ on account_type/is_active
    will produce the same account name.
    """
    seen: Dict[str, str] = {}
    duplicates: List[str] = []
    for config_name, account_config in account_configs:
        for model in models:
            account_name = build_account_name(model, account_config)
            if account_name in seen and seen[account_name] != config_name:
                duplicates.append(account_name)
                continue
            seen[account_name] = config_name

    if duplicates:
        dup_text = ", ".join(sorted(set(duplicates)))
        raise SystemExit(
            "Account naming collision detected under current rules. "
            f"Please adjust your account-combinations CSV to avoid duplicate names: {dup_text}"
        )


def ensure_schema_ready(db_base, db_engine) -> None:
    """
    Ensure required tables exist for standalone script execution.
    This avoids "no such table: users" when running before API startup.
    """
    db_base.metadata.create_all(bind=db_engine)


def main() -> int:
    args = parse_args()

    # Ensure sqlite relative path always points to backend/data.db.
    backend_dir = Path(__file__).resolve().parent.parent
    os.chdir(backend_dir)

    from database.connection import SessionLocal, engine, Base
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
    if args.mode == "all-combinations":
        validate_unique_account_names(account_configs, MODEL_LIST)

    ensure_schema_ready(Base, engine)

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
                name = build_account_name(model, account_config)
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
                        print(f"[UPDATED] {name} (model={model}, combo={config_name})")
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
                print(f"[CREATED] {name} (model={model}, combo={config_name})")

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
