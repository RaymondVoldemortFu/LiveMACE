#!/usr/bin/env python3
"""
Create AI trading accounts in batch using API credentials from .env.
Every run also ensures baseline accounts (buy_hold, grid) exist for the target user,
with duplicate check by agent_type only — not part of model batch creation.

Examples:
1) Create model accounts for all account config combinations from .env (default):
   python script/create_accounts_from_env.py

2) Update existing same-name accounts instead of skipping:
   python script/create_accounts_from_env.py --update-existing

3) Create one account per model using single-mode defaults from .env:
   python script/create_accounts_from_env.py --mode single

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
from typing import Dict, List, Tuple, Sequence, Type

import dotenv
from sqlalchemy import or_

MODEL_LIST = [
    # openai
    "gpt-5.4",
    # deepseek
    "deepseek-v3.2",
    # google
    "gemini-3.1-pro-preview",
    # xai
    # use official connect api to avoid tool calling issue
    # model end with -all is reverse engineered from official connect api, should be avoid
    "grok-4.20-beta-0309-reasoning",   
    # qwen
    "qwen3-max",
]

DEFAULT_AGENT_TYPE = "react"
DEFAULT_INITIAL_CAPITAL = Decimal("10000")
DEFAULT_PLACEHOLDER_ACCOUNT_NAME = "GPT"
# Non-LLM baselines: always ensured in main(), separate from MODEL_LIST batch logic.
BASELINE_AGENT_TYPES: Tuple[str, ...] = ("buy_hold", "grid")
SINGLE_CREATE_MODE = "single"
ALL_COMBINATIONS_MODE = "all-combinations"
DEFAULT_CREATE_MODE = ALL_COMBINATIONS_MODE
ACCOUNT_FIELD_KEYS = [
    "account_type",
    "agent_type",
    "memory_enabled",
    "tool_routing_enabled",
    "enable_rule_aware",
    "is_active",
]

BASELINE_ACCOUNT_SPECS = [
    {"name": "buy_hold", "agent_type": "buy_hold"},
    {"name": "grid", "agent_type": "grid"},
]

LLM_AGENT_TYPES = {"react", "multi_agent", "advanced_multi_agent", "rule_aware"}


def _agent_type_uses_llm(agent_type: str) -> bool:
    return (agent_type or "").strip().lower() in LLM_AGENT_TYPES


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
        choices=[SINGLE_CREATE_MODE, ALL_COMBINATIONS_MODE],
        default=DEFAULT_CREATE_MODE,
        help=(
            "Account creation mode: "
            "'all-combinations' (default) creates one account per model for each row in ACCOUNT_COMBO_CSV_PATH; "
            "'single' creates one account per model using ACCOUNT_DEFAULT_*."
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


def cleanup_default_gpt_account(
    db,
    AccountModel,
    user_id: int,
    account_name: str = DEFAULT_PLACEHOLDER_ACCOUNT_NAME,
    dependent_models: Sequence[Type] | None = None,
) -> Tuple[int, str]:
    """
    Remove default placeholder GPT account if exists for target user.
    Returns (deleted_count, message).
    """
    target_account_ids = [
        row[0]
        for row in db.query(AccountModel.id)
        .filter(AccountModel.user_id == user_id, AccountModel.name == account_name)
        .all()
    ]
    if not target_account_ids:
        return 0, f"No default placeholder account named '{account_name}' found"

    for model in dependent_models or []:
        if not hasattr(model, "account_id"):
            continue
        db.query(model).filter(model.account_id.in_(target_account_ids)).delete(synchronize_session=False)

    deleted_count = (
        db.query(AccountModel)
        .filter(AccountModel.user_id == user_id, AccountModel.name == account_name)
        .delete(synchronize_session=False)
    )
    if deleted_count > 0:
        return deleted_count, f"Removed default placeholder account '{account_name}'"
    return 0, f"Default placeholder account '{account_name}' was not removed"


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
    if mode == SINGLE_CREATE_MODE:
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


def ensure_baseline_accounts(
    db,
    user_id: int,
    AccountModel,
    update_existing: bool = False,
) -> Tuple[int, int, int]:
    """
    Create/update baseline accounts (buy_hold, grid) exactly once per spec.
    Idempotent: avoids duplicates by checking (name OR agent_type).
    """
    created = 0
    updated = 0
    skipped = 0
    for baseline_spec in BASELINE_ACCOUNT_SPECS:
        baseline_name = baseline_spec["name"]
        baseline_agent_type = baseline_spec["agent_type"]
        existing = (
            db.query(AccountModel)
            .filter(
                AccountModel.user_id == user_id,
                or_(
                    AccountModel.name == baseline_name,
                    AccountModel.agent_type == baseline_agent_type,
                ),
            )
            .first()
        )
        if existing:
            if update_existing:
                existing.model = None
                existing.base_url = None
                existing.api_key = None
                existing.account_type = "AI"
                existing.name = baseline_name
                existing.agent_type = baseline_agent_type
                existing.memory_enabled = "false"
                existing.tool_routing_enabled = "false"
                existing.enable_rule_aware = "false"
                existing.is_active = "true"
                updated += 1
                print(f"[BASELINE UPDATED] {baseline_name} (id={existing.id})")
                continue
            skipped += 1
            print(f"[BASELINE SKIP] {baseline_name} already exists (id={existing.id})")
            continue
        account = AccountModel(
            user_id=user_id,
            version="v1",
            name=baseline_name,
            account_type="AI",
            agent_type=baseline_agent_type,
            memory_enabled="false",
            tool_routing_enabled="false",
            enable_rule_aware="false",
            is_active="true",
            model=None,
            base_url=None,
            api_key=None,
            initial_capital=DEFAULT_INITIAL_CAPITAL,
            current_cash=DEFAULT_INITIAL_CAPITAL,
            frozen_cash=Decimal("0"),
        )
        db.add(account)
        created += 1
        print(f"[BASELINE CREATED] {baseline_name}")
    return created, updated, skipped


def main() -> int:
    args = parse_args()

    # Ensure sqlite relative path always points to backend/data.db.
    backend_dir = Path(__file__).resolve().parent.parent
    os.chdir(backend_dir)

    # Load .env from current/parent dirs but do not override process env.
    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True), override=False)
    from services.security.api_key_security import encrypt_api_key
    # Import DB modules only after .env is loaded so DATABASE_URL takes effect.
    from database.connection import SessionLocal, engine, Base
    from database.models import (
        Account,
        User,
        Position,
        Order,
        Trade,
        AIDecisionLog,
        AgentTrace,
        AgentPeriodCheckpoint,
        AgentMemory,
        AccountSnapshot,
        RuleEvaluationResult,
    )

    api_key = (os.getenv("API_KEY") or "").strip()
    encrypted_api_key = encrypt_api_key(api_key)

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
    baseline_created = 0
    baseline_updated = 0
    baseline_skipped = 0

    db = SessionLocal()
    try:
        user = ensure_user(db, args.user, User)
        deleted_count, cleanup_message = cleanup_default_gpt_account(
            db,
            Account,
            user.id,
            dependent_models=[
                Trade,
                Order,
                Position,
                AIDecisionLog,
                AgentTrace,
                AgentPeriodCheckpoint,
                AgentMemory,
                AccountSnapshot,
                RuleEvaluationResult,
            ],
        )
        if deleted_count > 0:
            db.commit()
            print(f"[CLEANUP] {cleanup_message}")
        else:
            print(f"[CLEANUP] {cleanup_message}")

        baseline_created, baseline_updated, baseline_skipped = ensure_baseline_accounts(
            db,
            user.id,
            Account,
            update_existing=args.update_existing,
        )

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
                        if _agent_type_uses_llm(account_config["agent_type"]):
                            existing.model = model
                            existing.base_url = base_url
                            existing.api_key = encrypted_api_key
                        else:
                            existing.model = None
                            existing.base_url = None
                            existing.api_key = None
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
                    model=model if _agent_type_uses_llm(account_config["agent_type"]) else None,
                    base_url=base_url if _agent_type_uses_llm(account_config["agent_type"]) else None,
                    api_key=encrypted_api_key if _agent_type_uses_llm(account_config["agent_type"]) else None,
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

    total = len(MODEL_LIST) * len(account_configs) + len(BASELINE_ACCOUNT_SPECS)
    print(
        f"Done. baseline_created={baseline_created}, baseline_updated={baseline_updated}, baseline_skipped={baseline_skipped}, "
        f"created={created}, updated={updated}, skipped={skipped}, total={total}, mode={args.mode}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
