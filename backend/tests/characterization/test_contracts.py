from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from alpha_arena.contracts import (
    AccountView,
    AgentRunResult,
    AgentRuntimeError,
    DecisionContext,
    ExecutedTradeRef,
    ExtensionRef,
    Market,
    PortfolioView,
    PositionView,
    PromptSpec,
    SideEffect,
    TerminationReason,
    ToolResult,
    ToolSpec,
    TradeCommand,
    TradeCommandResult,
    to_jsonable,
)


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone(timedelta(hours=8)))


def _portfolio() -> PortfolioView:
    account = AccountView(
        id=1,
        name="demo",
        initial_capital=Decimal("10000.00"),
        current_cash=Decimal("9000.00"),
        frozen_cash=Decimal("0.00"),
        margin_used=Decimal("0.00"),
    )
    position = PositionView(
        symbol="BTC",
        market=Market.CRYPTO,
        quantity=Decimal("0.02"),
        available_quantity=Decimal("0.02"),
        avg_cost=Decimal("50000.000000"),
        leverage=1,
        side="LONG",
    )
    return PortfolioView(
        account=account,
        positions=(position,),
        prices={"BTC": Decimal("51000.00")},
        total_assets=Decimal("10020.00"),
        captured_at=NOW,
    )


def test_public_dtos_are_frozen_and_nested_mappings_are_read_only():
    context = DecisionContext(
        account_id=1,
        decision_round_id="round-1",
        trace_id="trace-1",
        portfolio=_portfolio(),
        config={"max_steps": 10, "routing": {"preferred": ["core.execute_trade"]}},
        started_at=NOW,
    )

    with pytest.raises(FrozenInstanceError):
        context.trace_id = "changed"
    with pytest.raises(TypeError):
        context.config["max_steps"] = 20
    with pytest.raises(TypeError):
        context.portfolio.prices["BTC"] = Decimal("1")
    with pytest.raises(TypeError):
        context.config["routing"]["preferred"] = ()
    assert context.config["routing"]["preferred"] == ("core.execute_trade",)


def test_dto_validation_rejects_empty_ids_naive_times_and_invalid_leverage():
    with pytest.raises(ValueError, match="id"):
        ExtensionRef(id="", version="1.0.0")
    with pytest.raises(ValueError, match="timezone-aware"):
        PortfolioView(
            account=_portfolio().account,
            positions=(),
            prices={},
            total_assets=Decimal("1"),
            captured_at=datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="leverage"):
        PositionView("BTC", Market.CRYPTO, Decimal("1"), Decimal("1"), Decimal("1"), 0, "LONG")
    with pytest.raises(TypeError, match="market"):
        PositionView("BTC", "CRYPTO", Decimal("1"), Decimal("1"), Decimal("1"), 1, "LONG")


def test_stable_json_serialization_snapshot():
    context = DecisionContext(
        account_id=1,
        decision_round_id="round-1",
        trace_id="trace-1",
        portfolio=_portfolio(),
        config={"z": True, "a": Decimal("1.20")},
        started_at=NOW,
    )

    encoded = to_jsonable(context)
    assert encoded["started_at"] == "2026-07-17T04:00:00Z"
    assert encoded["portfolio"]["total_assets"] == "10020.00"
    assert list(encoded["config"]) == ["a", "z"]
    assert json.dumps(encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_agent_tool_prompt_and_trade_contract_shapes():
    trade_ref = ExecutedTradeRef("open", "BTC", Market.CRYPTO, 11, 21, True)
    result = AgentRunResult(
        trace_id="trace-1",
        decision_round_id="round-1",
        termination_reason=TerminationReason.TRADE_DONE,
        executed_trades=(trade_ref,),
        metadata={"agent": "core.react"},
    )
    assert result.executed_trades == (trade_ref,)

    spec = ToolSpec(
        name="example.quote",
        description="Read a quote",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect=SideEffect.READ_ONLY,
    )
    assert spec.timeout_seconds == 30.0
    assert ToolResult(ok=True, value={"price": 1}).ok is True
    with pytest.raises(ValueError, match="error_code"):
        ToolResult(ok=False)

    prompt = PromptSpec("example.system", "1.0.0", ("portfolio",))
    assert prompt.required_variables == ("portfolio",)

    command = TradeCommand(
        account_id=1,
        operation="open",
        market=Market.CRYPTO,
        symbol="BTC",
        direction="long",
        sizing_mode="portion",
        sizing_value=Decimal("0.1"),
        leverage=1,
        reason="test",
        idempotency_key="round-1:call-1",
    )
    command_result = TradeCommandResult(True, True, None, None, 11, 21, command)
    assert command_result.normalized_command is command


def test_public_error_serialization_is_stable_and_read_only():
    error = AgentRuntimeError("agent failed", details={"trace_id": "trace-1"})
    assert error.to_dict() == {
        "code": "AGENT_RUNTIME_ERROR",
        "message": "agent failed",
        "details": {"trace_id": "trace-1"},
    }
    with pytest.raises(TypeError):
        error.details["secret"] = "value"
