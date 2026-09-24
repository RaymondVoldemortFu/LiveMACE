from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from benchmark.contracts import TradeCommandResult
from tests.legacy_fixtures.agents import create_agent
from services.agent.tools import ToolRegistry
from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall


PORTFOLIO = {"total_assets": 10000.0, "cash": 10000.0, "positions": []}
PRICES = {"BTC": 50000.0}


class _CallerSession:
    def expire_all(self):
        return None


class _ContainerService:
    pass


class _SearchAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def run(self, *args):
        return {"results": []}


def _registered_default_tools(monkeypatch, captured_commands):
    import benchmark.application.trading as trading_app
    from tests.legacy_fixtures import tools as env_wrapper

    account = SimpleNamespace(
        model="test-model",
        api_key="encrypted-placeholder",
        base_url=None,
        name="test-agent",
        memory_enabled="false",
    )

    class Gateway:
        def execute(self, command):
            captured_commands.append(command)
            return TradeCommandResult(
                accepted=True,
                executed=True,
                reject_code=None,
                reject_message=None,
                order_id=11,
                trade_id=12,
                normalized_command=command,
            )

    monkeypatch.setattr(env_wrapper, "get_account", lambda db, account_id: account)
    monkeypatch.setattr(env_wrapper, "ContainerService", _ContainerService)
    monkeypatch.setattr(env_wrapper, "SearchSubAgent", _SearchAgent)
    monkeypatch.setattr(trading_app, "get_default_trade_gateway", lambda: Gateway())

    registry = ToolRegistry()
    env_wrapper.register_default_tools(
        registry,
        _CallerSession(),
        account_id=7,
        trace_id="trace-production-chain",
        runtime_api_key="runtime-key",
    )
    return registry


def test_registered_trade_tool_receives_runtime_owned_stable_key(monkeypatch):
    captured_commands = []
    registry = _registered_default_tools(monkeypatch, captured_commands)
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "provider-call-9",
                        "execute_trade",
                        json.dumps({"operation": "hold", "reason": "test"}),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    agent = create_agent("react", llm, registry, max_steps=3)
    agent.set_tool_routing_enabled(False)

    result = agent.run(
        PORTFOLIO,
        PRICES,
        trace_id="trace-production-chain",
        decision_round_id="decision-round-4",
    )

    assert len(captured_commands) == 1
    assert captured_commands[0].idempotency_key == "decision-round-4:provider-call-9"
    assert result["executed_trades"][0]["executed"] is True


@pytest.mark.parametrize(
    "field",
    ["idempotency_key", "decision_round_id", "tool_call_id"],
)
def test_llm_cannot_supply_trade_runtime_identity(monkeypatch, field):
    captured_commands = []
    registry = _registered_default_tools(monkeypatch, captured_commands)
    args = {"operation": "hold", field: "model-controlled"}
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("provider-call-1", "execute_trade", json.dumps(args))],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    agent = create_agent("react", llm, registry, max_steps=3)
    agent.set_tool_routing_enabled(False)

    result = agent.run(
        PORTFOLIO,
        PRICES,
        trace_id="trace-forged-runtime-field",
        decision_round_id="decision-round-1",
    )

    assert captured_commands == []
    trade_result = result["executed_trades"][0]
    assert "TOOL_RUNTIME_ARGUMENT_FORBIDDEN" in trade_result["error"]


def test_trade_call_without_decision_round_fails_explicitly(monkeypatch):
    captured_commands = []
    registry = _registered_default_tools(monkeypatch, captured_commands)
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("provider-call-1", "execute_trade", '{"operation":"hold"}')],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    agent = create_agent("react", llm, registry, max_steps=3)
    agent.set_tool_routing_enabled(False)

    result = agent.run(PORTFOLIO, PRICES, trace_id="trace-missing-round")

    assert captured_commands == []
    assert "DECISION_ROUND_ID_REQUIRED" in result["executed_trades"][0]["error"]
