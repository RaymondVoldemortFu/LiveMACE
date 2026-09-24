"""Final refactor contracts exercise boundaries and persisted results."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import inspect
import logging
from types import SimpleNamespace

import pytest

from benchmark.application.trading import planner
from benchmark.extensions import ExtensionSettings, build_extension_runtime


def test_named_toolsets_resolve_union_and_opt_out():
    runtime = build_extension_runtime(ExtensionSettings())
    catalog = runtime.catalog
    selected = catalog.resolve_tool_names(("core.trading-tools", "core.account-tools"))
    assert "core.execute_trade" in selected
    assert not any(name.startswith("public.") for name in selected)
    assert "core.execute_trade" not in catalog.resolve_tool_names(
        ("core.trading-tools",), ("core.execute_trade",)
    )
    assert catalog.resolve_tool_names() == tuple(
        sorted(spec.name for spec in runtime.tools.list())
    )
    report = catalog.validate_account_config(
        {"agent_id": "core.react", "toolset_ids": ["core.trading-tools"]}
    )
    assert report.valid, report.errors
    assert list(report.normalized_config["toolset_ids"]) == ["core.trading-tools"]
    with pytest.raises(ValueError, match="Unknown toolsets"):
        catalog.resolve_tool_names(("missing.tools",))


def test_planner_is_pure_and_rejection_cannot_mutate_inputs():
    account = dict(id=1, current_cash=1000, frozen_cash=0, margin_used=0)
    order = dict(
        id=1,
        account_id=1,
        symbol="BTC",
        name="BTC",
        market="CRYPTO",
        side="LONG",
        quantity=1,
        leverage=2,
        order_no="order",
        filled_quantity=0,
        status="PENDING",
    )
    before = deepcopy((account, order))
    plan = planner.plan_crypto(
        account,
        None,
        order,
        side="LONG",
        quantity=1,
        leverage=2,
        exec_price=Decimal(100),
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert (account, order) == before
    assert plan.account["current_cash"] == 949.93
    assert plan.account["margin_used"] == 50
    assert plan.position["quantity"] == 1
    assert plan.trade["taker_fee"] == 0.07
    with pytest.raises(ValueError, match="Insufficient cash"):
        planner.plan_crypto(
            account,
            None,
            order,
            side="LONG",
            quantity=100,
            leverage=2,
            exec_price=Decimal(100),
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    assert (account, order) == before
    source = inspect.getsource(planner)
    for forbidden in (
        "sqlalchemy",
        "database.",
        ".query(",
        "Session",
        "get_last_price",
    ):
        assert forbidden not in source


def test_event_id_survives_persistence_failure_and_secrets_are_redacted(caplog):
    from benchmark.persistence.events import PersistentEventSink

    context = SimpleNamespace(account_id=1, trace_id="trace", decision_round_id="round")

    def broken_session():
        raise RuntimeError("unavailable")

    sink = PersistentEventSink(
        context, secrets=("secret-value",), session_factory=broken_session
    )
    with caplog.at_level(logging.INFO, logger="benchmark.persistence.events"):
        sink.record(
            "run.configured",
            {"api_key": "secret-value", "nested": {"text": "secret-value"}},
        )
    event = next(
        record.runtime_event
        for record in caplog.records
        if hasattr(record, "runtime_event")
    )
    failed = next(record for record in caplog.records if hasattr(record, "event_id"))
    assert event["event_id"] == failed.event_id
    assert event["trace_id"] == "trace"
    assert "secret-value" not in caplog.text


def test_all_api_success_json_responses_have_contracts():
    from benchmark.bootstrap.app import create_app
    from benchmark.bootstrap.runtime import StartupMode

    schema = create_app(mode=StartupMode.NO_BACKGROUND).openapi()
    for path, methods in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        for method, operation in methods.items():
            for status, response in operation.get("responses", {}).items():
                content = response.get("content", {})
                if status.startswith("2") and "application/json" in content:
                    assert content["application/json"].get("schema"), (method, path)
