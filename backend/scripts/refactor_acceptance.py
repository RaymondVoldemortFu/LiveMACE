"""Run four real LLM Agents in a fresh MySQL/Redis environment and verify their ledger.

Usage: python scripts/refactor_acceptance.py --credentials ../.wave3/runtime.env
The credential file supplies model/provider settings only. Dedicated temporary
containers and a unique sandbox label isolate this run from existing projects.
--serve-after opens an HTTP service only after all automatic checks pass.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def configure(args, output):
    from dotenv import dotenv_values

    for key, value in dotenv_values(args.credentials).items():
        if value is not None:
            os.environ[key] = value
    if not os.getenv("API_KEY") or not os.getenv("BASE_URL"):
        raise ValueError("Credential file must supply API_KEY and BASE_URL")
    os.environ.update(
        WAVE3_PRODUCTION="false",
        ENABLE_ACCOUNT_CREATION_API="true",
        ENABLE_ACCOUNT_UPDATE_API="true",
        ENABLE_MANUAL_ORDER_API="true",
        DECISION_OPERATOR_TOKEN=secrets.token_urlsafe(32),
        SANDBOX_INSTANCE="refactor-acceptance-" + uuid4().hex[:10],
        DOCKER_POOL_MAX_SIZE="2",
        DOCKER_POOL_MAX_OVERFLOW="0",
        AGENT_ROUND_TIMEOUT_SECONDS=str(args.round_timeout),
        AGENT_LLM_CALL_LIMIT="80",
        LLM_MAX_OUTPUT_TOKENS="4096",
        LLM_REQUEST_TIMEOUT_SECONDS="60",
        LLM_MAX_RETRIES="1",
        TOOL_CACHE_ENABLED="true",
        LIVEMACE_BENCH_EXTENSION_DIRS="",
        LIVEMACE_BENCH_DISABLED_EXTENSIONS="",
    )
    os.environ["TOOL_CACHE_KEY_PREFIX"] = os.environ["SANDBOX_INSTANCE"]
    os.environ["DOCKER_HOST"] = subprocess.check_output(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        text=True,
    ).strip()
    from cryptography.fernet import Fernet

    os.environ["API_KEY_CIPHER_KEY"] = Fernet.generate_key().decode()

    class RedactedFormatter(logging.Formatter):
        def format(self, record):
            text = super().format(record)
            for key, value in os.environ.items():
                if value and any(word in key for word in ("KEY", "TOKEN", "PASSWORD")):
                    text = text.replace(value, "[REDACTED]")
            return text

    handler = logging.FileHandler(output / "runtime.log", encoding="utf-8")
    handler.setFormatter(
        RedactedFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def start_dependencies(containers):
    import docker

    client = docker.from_env()
    label = os.environ["SANDBOX_INSTANCE"]
    password = secrets.token_urlsafe(24)
    mysql = client.containers.run(
        "mysql:8.4",
        detach=True,
        name=label + "-mysql",
        environment={
            "MYSQL_ROOT_HOST": "%",
            "MYSQL_ROOT_PASSWORD": password,
            "MYSQL_DATABASE": "refactor_acceptance",
        },
        ports={"3306/tcp": ("127.0.0.1", None)},
        labels={"refactor.acceptance": label},
    )
    containers.append(mysql)
    redis = client.containers.run(
        "valkey/valkey:8-alpine",
        detach=True,
        name=label + "-redis",
        ports={"6379/tcp": ("127.0.0.1", None)},
        labels={"refactor.acceptance": label},
    )
    containers.append(redis)

    def published_port(container, port):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            container.reload()
            bindings = (
                container.attrs.get("NetworkSettings", {}).get("Ports", {}).get(port)
            )
            if bindings:
                return bindings[0]["HostPort"]
            if container.status in ("exited", "dead"):
                raise RuntimeError(
                    "Acceptance dependency exited before publishing port"
                )
            time.sleep(0.2)
        raise RuntimeError("Acceptance dependency port publication timed out")

    mysql_port = published_port(mysql, "3306/tcp")
    redis_port = published_port(redis, "6379/tcp")
    os.environ["DATABASE_URL"] = (
        f"mysql+pymysql://root:{password}@127.0.0.1:{mysql_port}/refactor_acceptance?charset=utf8mb4"
    )
    os.environ["MYSQL_TEST_DATABASE_URL"] = os.environ["DATABASE_URL"].replace(
        "/refactor_acceptance?", "/livemace_bench_test?"
    )
    os.environ["TOOL_CACHE_REDIS_URL"] = f"redis://127.0.0.1:{redis_port}/0"
    import pymysql

    deadline = time.monotonic() + 150
    while True:
        try:
            connection = pymysql.connect(
                host="127.0.0.1",
                port=int(mysql_port),
                user="root",
                password=password,
                database="refactor_acceptance",
                connect_timeout=2,
            )
            with connection.cursor() as cursor:
                cursor.execute("CREATE DATABASE IF NOT EXISTS livemace_bench_test")
            connection.close()
            break
        except pymysql.MySQLError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Acceptance MySQL did not become ready") from None
            time.sleep(1)
    print("Dedicated MySQL and Redis ready", flush=True)
    return client


def verify_ledger(account, orders, trades, positions, receipts, result):
    """Independent replay of persisted fills, without calling production planners."""
    from decimal import Decimal as D

    by_order = {row.id: row for row in orders}
    fills = {row.order_id: row.id for row in trades}
    refs = [r for r in result["executed_trades"] if r["executed"]]
    assert {r["order_id"] for r in refs} == set(fills), "Agent result/fill mismatch"
    for ref in refs:
        assert ref["trade_id"] is None or ref["trade_id"] == fills[ref["order_id"]]
    receipt_orders = set()
    for receipt in receipts:
        data = json.loads(receipt.result_json)
        assert data["normalized_command"]["account_id"] == account.id
        if data["executed"]:
            if data["order_id"] is not None:
                receipt_orders.add(data["order_id"])
                assert data["order_id"] in fills
                assert (
                    data["trade_id"] is None
                    or data["trade_id"] == fills[data["order_id"]]
                )
            for closed in data["raw_result"].get("closed_orders", []):
                receipt_orders.add(closed["order_id"])
    assert receipt_orders == set(fills), "Receipt/fill mismatch"
    cash, margin, inventory = D(account.initial_capital), D(0), {}
    for trade in sorted(trades, key=lambda row: row.id):
        order = by_order[trade.order_id]
        qty, price, leverage = D(trade.quantity), D(trade.price), D(order.leverage)
        assert (
            qty > 0
            and price > 0
            and abs(qty - D(str(order.filled_quantity)))
            <= max(D("0.00000001"), qty * D("0.000005"))
        ), "Order FLOAT text protocol / trade DECIMAL quantity mismatch"
        key = (trade.market, trade.symbol)
        if trade.market == "US":
            assert leverage == 1
            fee = max(qty * price * D("0.001"), D("0.1"))
            assert abs(D(trade.commission) - fee) <= D("0.00001"), (
                "US commission mismatch"
            )
            assert D(trade.taker_fee) == 0 and D(trade.interest_charged) == 0
            cash += qty * price * (1 if order.side == "SELL" else -1) - D(
                trade.commission
            )
            side = "LONG" if order.side == "BUY" else "SHORT"
            old_qty, cost, old_side, _ = inventory.get(key, (D(0), D(0), side, D(1)))
            if old_qty == 0 or old_side == side:
                cost = (old_qty * cost + qty * price) / (old_qty + qty)
                inventory[key] = (old_qty + qty, cost, side, D(1))
            else:
                assert qty <= old_qty
                inventory[key] = (old_qty - qty, cost, old_side, D(1))
            continue
        assert trade.market == "CRYPTO", "Unsupported fill market"
        assert abs(D(trade.taker_fee) - qty * price * D("0.0007")) < D("0.00001")
        assert D(trade.commission) == D(trade.taker_fee)
        cash -= D(trade.taker_fee) + D(trade.interest_charged)
        key = (trade.market, trade.symbol)
        if order.side in ("LONG", "SHORT"):
            previous = inventory.get(key, (D(0), D(0), order.side, leverage))
            old_qty, cost, side, old_leverage = previous
            assert old_qty == 0 or (side == order.side and old_leverage == leverage)
            cost = (old_qty * cost + qty * price) / (old_qty + qty)
            inventory[key] = (old_qty + qty, cost, order.side, leverage)
            cash -= qty * price / leverage
            if leverage > 1:
                margin += qty * price / leverage
        else:
            old_qty, cost, side, leverage = inventory[key]
            assert qty <= old_qty
            if leverage > 1:
                released = qty * cost / leverage
                cash += released + qty * (price - cost) * (1 if side == "LONG" else -1)
                margin -= released
            else:
                assert order.side == "SELL"
                cash += qty * price
            inventory[key] = (old_qty - qty, cost, side, leverage)
    assert abs(cash - D(account.current_cash)) <= D("0.01") * (len(trades) + 1), (
        "Cash replay mismatch"
    )
    assert abs(margin - D(account.margin_used)) <= D("0.01") * (len(trades) + 1), (
        "Margin replay mismatch"
    )
    actual = {(p.market, p.symbol): p for p in positions if p.quantity > 0}
    expected = {key: value for key, value in inventory.items() if value[0] > 0}
    assert actual.keys() == expected.keys(), "Position set mismatch"
    for key, (qty, cost, side, leverage) in expected.items():
        row = actual[key]
        assert abs(D(row.quantity) - qty) <= D("0.00000001")
        assert abs(D(row.available_quantity) - qty) <= D("0.00000001")
        assert abs(D(row.avg_cost) - cost) <= D("0.000001")
        assert row.side == side and row.leverage == leverage


def verify_accounts(account_ids, round_id, output):
    from database.connection import SessionLocal
    from database.models import (
        Account,
        RuntimeEvent,
        AIDecisionLog,
        Order,
        Trade,
        Position,
        TradeCommandReceipt,
    )

    reports = []
    with SessionLocal() as db:
        for account_id in account_ids:
            account = db.get(Account, account_id)
            events = (
                db.query(RuntimeEvent)
                .filter_by(account_id=account_id, decision_round_id=round_id)
                .order_by(RuntimeEvent.sequence)
                .all()
            )
            parsed = [
                {
                    "id": event.id,
                    "type": event.event_type,
                    "payload": json.loads(event.payload),
                }
                for event in events
            ]
            (output / f"account-{account_id}-events.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2)
            )
            completed = [event for event in parsed if event["type"] == "llm.completed"]
            assert completed, f"Account {account_id}: no completed real LLM request"
            assert any(event["type"] == "prompt.rendered" for event in parsed), (
                "missing prompt provenance"
            )
            results = [
                event["payload"] for event in parsed if event["type"] == "run.result"
            ]
            assert len(results) == 1 and results[0]["termination_reason"] in (
                "hold",
                "trade_done",
            ), (account_id, results)
            assert not any(event["type"] == "run.failed" for event in parsed), (
                account_id
            )
            trace_id = results[0]["trace_id"]
            summaries = (
                db.query(AIDecisionLog)
                .filter_by(
                    account_id=account_id, trace_id=trace_id, operation="summary"
                )
                .all()
            )
            assert len(summaries) == 1 and summaries[0].executed == "false", (
                "round summary must not duplicate execution"
            )
            orders = db.query(Order).filter_by(account_id=account_id).all()
            trades = db.query(Trade).filter_by(account_id=account_id).all()
            receipts = (
                db.query(TradeCommandReceipt).filter_by(account_id=account_id).all()
            )
            assert len({row.order_id for row in trades}) == len(trades), (
                "duplicate fill per order"
            )
            assert {row.order_id for row in trades} == {
                row.id for row in orders if row.status == "FILLED"
            }
            assert all(row.status == "COMPLETED" for row in receipts), (
                "incomplete receipt"
            )
            assert all(
                db.get(Order, row.order_id).account_id == account_id for row in trades
            ), "cross-account fill"
            positions = db.query(Position).filter_by(account_id=account_id).all()
            assert all(row.quantity >= 0 for row in positions), (
                "negative position quantity"
            )
            ledger_evidence = {
                "account": {
                    k: getattr(account, k)
                    for k in (
                        "id",
                        "initial_capital",
                        "current_cash",
                        "margin_used",
                        "frozen_cash",
                    )
                },
                "orders": [
                    {
                        k: getattr(row, k)
                        for k in ("id", "side", "leverage", "filled_quantity")
                    }
                    for row in orders
                ],
                "trades": [
                    {
                        k: getattr(row, k)
                        for k in (
                            "id",
                            "order_id",
                            "market",
                            "symbol",
                            "side",
                            "price",
                            "quantity",
                            "taker_fee",
                            "commission",
                            "interest_charged",
                        )
                    }
                    for row in trades
                ],
                "positions": [
                    {
                        k: getattr(row, k)
                        for k in (
                            "market",
                            "symbol",
                            "quantity",
                            "available_quantity",
                            "avg_cost",
                            "side",
                            "leverage",
                        )
                    }
                    for row in positions
                ],
                "receipts": [json.loads(row.result_json) for row in receipts],
                "result": results[0],
            }
            (output / f"account-{account_id}-ledger.json").write_text(
                json.dumps(ledger_evidence, ensure_ascii=False, indent=2, default=str)
            )
            verify_ledger(account, orders, trades, positions, receipts, results[0])
            item = dict(
                account_id=account_id,
                agent=account.agent_type,
                trace_id=trace_id,
                termination_reason=results[0]["termination_reason"],
                llm_completions=len(completed),
                cash=str(account.current_cash),
                margin_used=str(account.margin_used),
                orders=len(orders),
                trades=len(trades),
                receipts=len(receipts),
                positions=[
                    dict(symbol=p.symbol, quantity=str(p.quantity), side=p.side)
                    for p in positions
                ],
            )
            reports.append(item)
            (output / f"account-{account_id}-events.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2)
            )
    return reports


def run(args, output, mysql):
    from benchmark.bootstrap.app import create_app, AppSettings
    from benchmark.bootstrap.runtime import StartupMode
    from fastapi.testclient import TestClient
    from services.tool_cache import tool_cache
    from schemas.control_plane import PortfolioSnapshot

    app = create_app(
        AppSettings(static_dir=str(ROOT / "frontend/dist")),
        mode=StartupMode.NO_BACKGROUND,
    )
    agent_ids = (
        "core.react",
        "core.multi-agent",
        "core.advanced-multi-agent",
        "core.rule-aware",
    )
    account_ids = []
    with TestClient(app) as http:
        tool_cache.ensure_ready()
        assert http.get("/api/ready").status_code == 200
        # Separate schema on our temporary MySQL; integration tests may reset it.
        from sqlalchemy import create_engine
        from database.models import Base

        test_engine = create_engine(os.environ["MYSQL_TEST_DATABASE_URL"])
        Base.metadata.create_all(test_engine)
        test_engine.dispose()
        with (output / "mysql-tests.log").open("w") as log:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/trading/test_gateway_mysql_concurrency.py",
                    "-m",
                    "integration",
                    "-q",
                    "--tb=short",
                ],
                cwd=ROOT / "backend",
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        assert process.returncode == 0, (
            "MySQL concurrency tests failed; see mysql-tests.log"
        )
        print("MySQL concurrency checks passed", flush=True)
        for agent_id in agent_ids:
            created = http.post(
                "/api/account/",
                json=dict(
                    name="Acceptance " + agent_id,
                    account_type="MANUAL",
                    agent_type=agent_id.removeprefix("core.").replace("-", "_"),
                    model=os.getenv("WAVE3_MODEL", "deepseek-flash"),
                    base_url=os.environ["BASE_URL"],
                    api_key=os.environ["API_KEY"],
                    initial_capital=10000,
                    memory_enabled="false",
                    tool_routing_enabled="false",
                ),
            )
            assert created.status_code == 200, (created.status_code, created.text)
            account_id = created.json()["id"]
            account_ids.append(account_id)
            config = dict(
                agent_id=agent_id,
                toolset_ids=["core.default-tools"],
                disabled_tools=[],
                agent_config={"max_steps": 12},
                prompt_profile_id=agent_id + ".default",
            )
            if agent_id == "core.react":
                config["agent_config"].update(
                    memory_enabled=False, tool_routing_enabled=False
                )
            valid = http.post(
                f"/api/account/{account_id}/runtime-config/validate",
                json={"config": config},
            )
            assert valid.status_code == 200 and valid.json()["valid"], valid.text
            current = http.get(f"/api/account/{account_id}/runtime-config").json()
            saved = http.put(
                f"/api/account/{account_id}/runtime-config",
                json={
                    "config": valid.json()["config"],
                    "expected_updated_at": current["updated_at"],
                },
            )
            assert saved.status_code == 200, saved.text
        # Diagnostic wrapper preserves the real worker and its exception behavior.
        from benchmark.application.decisions import runner

        original_worker = runner.run_account

        def observed_worker(*worker_args, **worker_kwargs):
            try:
                return original_worker(*worker_args, **worker_kwargs)
            except Exception:
                logging.exception("Acceptance worker failed")
                raise

        runner.run_account = observed_worker
        print(
            "Four configured Agent accounts ready; starting real model decisions",
            flush=True,
        )
        response = http.post(
            "/api/agent/round",
            json={"account_ids": account_ids, "max_concurrency": 2},
            headers={"x-operator-token": os.environ["DECISION_OPERATOR_TOKEN"]},
        )
        assert response.status_code == 200, (response.status_code, response.text)
        result = response.json()
        (output / "round.json").write_text(json.dumps(result, indent=2))
        print("Agent round finished: " + json.dumps(result), flush=True)
        # Preserve the real database before validating so a verifier failure can
        # be diagnosed from the exact run, without another paid model decision.
        import gzip

        dumped = mysql.exec_run(
            [
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --skip-comments refactor_acceptance',
            ]
        )
        assert dumped.exit_code == 0, "Acceptance database export failed"
        (output / "database.sql.gz").write_bytes(gzip.compress(dumped.output))
        reports = verify_accounts(account_ids, result["decision_round_id"], output)
        assert result["processed_accounts"] == 4 and not result["errors"], result
        assert sum(item["trades"] for item in reports) > 0, (
            "No real fills; trade coverage incomplete"
        )
        for account_id in account_ids:
            for suffix in ("overview", "runtime-config"):
                assert (
                    http.get(f"/api/account/{account_id}/{suffix}").status_code == 200
                )
            latest = http.get(f"/api/agent/latest/{account_id}").json()
            assert (
                latest["trace_id"]
                and http.get("/api/agent/trace/" + latest["trace_id"]).status_code
                == 200
            )
        with http.websocket_connect("/ws") as ws:
            ws.send_json(
                {"type": "bootstrap", "username": "default", "initial_capital": 10000}
            )
            assert ws.receive_json()["type"] == "bootstrap_ok"
            PortfolioSnapshot.model_validate(ws.receive_json())
            ws.send_json({"type": "switch_account", "account_id": account_ids[0]})
            assert ws.receive_json()["type"] == "account_switched"
            PortfolioSnapshot.model_validate(ws.receive_json())
        from services.scheduler import task_scheduler

        assert not task_scheduler.is_running(), "unexpected recurring scheduler"
        report = dict(
            passed=True,
            model=os.getenv("WAVE3_MODEL", "deepseek-flash"),
            completed_at=datetime.now(timezone.utc).isoformat(),
            round_id=result["decision_round_id"],
            accounts=reports,
            mysql_concurrency="passed",
            websocket="passed",
            scheduler="stopped",
        )
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(
            "PASS: four real Agents, persisted events/ledger, MySQL locking, HTTP and WebSocket",
            flush=True,
        )
    from services.container_service import ContainerService

    ContainerService().shutdown()
    if args.serve_after:
        import uvicorn

        print(
            f"Agent checks complete. Browser check available at http://127.0.0.1:{args.port}",
            flush=True,
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, default=ROOT / ".wave3/runtime.env")
    parser.add_argument("--serve-after", action="store_true")
    parser.add_argument("--port", type=int, default=5688)
    parser.add_argument("--round-timeout", type=int, default=600)
    args = parser.parse_args()
    output = ROOT / ".refactor-acceptance" / datetime.now().strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, mode=0o700)
    os.umask(0o077)
    containers = []
    try:
        configure(args, output)
        print("Evidence: " + str(output), flush=True)
        start_dependencies(containers)
        run(args, output, containers[0])
    finally:
        # Remove only the containers created by this process.
        from services.container_service import ContainerService

        try:
            if ContainerService._instance is not None:
                ContainerService._instance.shutdown()
        except Exception:
            logging.exception("Sandbox cleanup failed")
        for container in reversed(containers):
            try:
                container.remove(force=True, v=True)
            except Exception:
                logging.exception("Acceptance container cleanup failed")
        print("Acceptance resources stopped", flush=True)


if __name__ == "__main__":
    main()
