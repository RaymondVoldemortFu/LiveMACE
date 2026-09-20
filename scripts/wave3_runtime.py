#!/usr/bin/env python3
"""Operate only the isolated local Wave3 schema; never import the source dataset."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from dotenv import dotenv_values  # noqa: E402


def configure():
    for key, value in dotenv_values(ROOT / ".wave3/runtime.env").items():
        if value is not None:
            os.environ[key] = value
    values = dotenv_values(ROOT / ".wave3/compose.env")
    os.environ["DATABASE_URL"] = (
        f"mysql+pymysql://wave3:{values['WAVE3_DB_PASSWORD']}@127.0.0.1:13306/alpha_arena_wave3?charset=utf8mb4"
    )
    os.environ["TOOL_CACHE_REDIS_URL"] = "redis://127.0.0.1:16379/0"
    os.environ["TOOL_CACHE_KEY_PREFIX"] = "wave3:iex"
    os.environ["WAVE3_PRODUCTION"] = "true"
    from database.safety import validate_database_target

    validate_database_target(os.environ["DATABASE_URL"])


def record_fingerprint():
    from datetime import datetime, timezone

    path = ROOT / "alpha_arena_final.sqlite"
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    reference = ROOT / ".wave3/evidence/source-database.json"
    reference.parent.mkdir(parents=True, exist_ok=True)
    with reference.open("x") as output:
        json.dump(
            {
                "path": str(path),
                "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
                "sha256": digest.hexdigest(),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
            output,
            indent=2,
        )
    print("Recorded read-only source database fingerprint")


def fingerprint():
    reference = json.loads((ROOT / ".wave3/evidence/source-database.json").read_text())
    path = Path(reference["path"])
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = {
        "size": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": digest.hexdigest(),
    }
    assert all(actual[key] == reference[key] for key in actual), (
        "Source database fingerprint changed"
    )
    print(json.dumps({"source_unchanged": True, **actual}))


def seed():
    from database.connection import SessionLocal
    from database.models import Account
    from benchmark.bootstrap.seed import ensure_default_user
    from services.security.api_key_security import encrypt_api_key
    from services.extension_config_service import get_extension_config_service

    with SessionLocal() as db:
        user, _ = ensure_default_user(db)
        ids = []
        for agent in (
            "react",
            "multi_agent",
            "advanced_multi_agent",
            "rule_aware",
            "buy_hold",
            "grid",
        ):
            name = f"Wave3 {agent}"
            account = db.query(Account).filter(Account.name == name).first()
            if account is None:
                account = Account(
                    user_id=user.id,
                    name=name,
                    account_type="MANUAL",
                    agent_type=agent,
                    model=os.environ["WAVE3_MODEL"],
                    base_url=os.environ.get("BASE_URL"),
                    api_key=encrypt_api_key(os.environ.get("API_KEY")),
                    initial_capital=10000,
                    current_cash=10000,
                    frozen_cash=0,
                    margin_used=0,
                    memory_enabled="false",
                    tool_routing_enabled="false",
                    enable_rule_aware="true" if agent == "rule_aware" else "false",
                )
                db.add(account)
                db.flush()
            ids.append((account.id, agent))
        db.commit()
    service = get_extension_config_service()
    for aid, agent in ids:
        if agent in ("buy_hold", "grid"):
            continue
        state = service.get_runtime_config(aid)
        if state["updated_at"] is None:
            config = state["config"]
            config["agent_config"]["max_steps"] = 12
            service.save(aid, config, None)
    print(
        json.dumps(
            {
                "accounts": [{"id": aid, "agent": agent} for aid, agent in ids],
                "scheduling": "manual until provider validation",
            }
        )
    )


def status():
    from database.connection import SessionLocal
    from database.models import Account, RuntimeEvent, Trade, Order

    with SessionLocal() as db:
        print(
            json.dumps(
                {
                    "accounts": [
                        {
                            "id": a.id,
                            "name": a.name,
                            "type": a.account_type,
                            "cash": str(a.current_cash),
                        }
                        for a in db.query(Account).all()
                    ],
                    "events": db.query(RuntimeEvent).count(),
                    "orders": db.query(Order).count(),
                    "trades": db.query(Trade).count(),
                }
            )
        )


def validate_model_probe(probe_path, accounts):
    """Require evidence for the provider each selected AI account will use."""
    try:
        report = json.loads(Path(probe_path).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Complete the real model/tool probe before AI scheduling"
        ) from exc

    def identity(model, base_url):
        return (str(model or "").strip(), str(base_url or "").strip().rstrip("/"))

    expected = identity(os.getenv("WAVE3_MODEL"), os.getenv("BASE_URL"))
    if not all(expected):
        raise ValueError("WAVE3_MODEL and BASE_URL are required for AI scheduling")
    if (
        not isinstance(report, dict)
        or report.get("passed") is not True
        or identity(report.get("model"), report.get("base_url")) != expected
    ):
        raise ValueError(
            "A successful model/tool probe for the current model and base URL is required"
        )
    for account in accounts:
        if identity(account.model, account.base_url) != expected:
            raise ValueError(
                f"Account {account.id} model/base URL differs from the validated provider"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "record-fingerprint",
            "fingerprint",
            "schema",
            "seed",
            "status",
            "round",
            "schedule",
            "pause",
        ],
    )
    parser.add_argument("--accounts", type=int, nargs="+")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    if args.action == "record-fingerprint":
        return record_fingerprint()
    if args.action == "fingerprint":
        return fingerprint()
    configure()
    if args.action == "schema":
        from benchmark.bootstrap.schema import run_schema_bootstrap

        print(run_schema_bootstrap())
    elif args.action == "seed":
        seed()
    elif args.action == "status":
        status()
    elif args.action in {"schedule", "pause"}:
        if not args.accounts:
            parser.error("schedule/pause requires explicit --accounts")
        from database.connection import SessionLocal
        from database.models import Account
        from services.baselines import is_baseline_trading_account

        with SessionLocal() as db:
            accounts = (
                db.query(Account)
                .filter(Account.id.in_(args.accounts), Account.is_active == "true")
                .all()
            )
            if {a.id for a in accounts} != set(args.accounts):
                parser.error("Select existing active accounts")
            ai_accounts = [a for a in accounts if not is_baseline_trading_account(a)]
            if args.action == "schedule" and ai_accounts:
                try:
                    validate_model_probe(
                        ROOT / ".wave3/evidence/model-probe-final.json", ai_accounts
                    )
                except ValueError as exc:
                    parser.error(str(exc))
            for account in accounts:
                account.account_type = "AI" if args.action == "schedule" else "MANUAL"
            db.commit()
        print(json.dumps({"action": args.action, "account_ids": args.accounts}))
    else:
        import urllib.request

        if not args.accounts:
            parser.error("round requires explicit --accounts")
        token = os.environ.get("DECISION_OPERATOR_TOKEN")
        if not token:
            parser.error("DECISION_OPERATOR_TOKEN is not configured")
        request = urllib.request.Request(
            "http://127.0.0.1:15611/api/agent/round",
            data=json.dumps(
                {"account_ids": args.accounts, "max_concurrency": args.concurrency}
            ).encode(),
            headers={"Content-Type": "application/json", "X-Operator-Token": token},
            method="POST",
        )
        timeout = (
            len(args.accounts) * float(os.getenv("AGENT_ROUND_TIMEOUT_SECONDS", "600"))
            + 600
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            print(response.read().decode())


if __name__ == "__main__":
    main()
