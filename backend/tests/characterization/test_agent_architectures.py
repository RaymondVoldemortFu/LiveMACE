from __future__ import annotations

import inspect
import json

import pytest

from tests.legacy_fixtures.agents import create_agent
from services.agent.multi_agent import MultiAgent
from services.agent.multi_agent_advanced import AdvancedMultiAgent
from services.agent.react import ReActAgent
from services.agent.rule_aware import RuleAwareAgent
from services.agent.tools import Tool, ToolRegistry
from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall


PORTFOLIO = {"total_assets": 10000.0, "cash": 10000.0, "positions": []}
PRICES = {"BTC": 50000.0}


def _registry_with_trade(sequence=None):
    registry = ToolRegistry()
    events = sequence if sequence is not None else []

    def execute_trade(**kwargs):
        events.append("tool_started")
        events.append("tool_finished")
        return {"executed": True, "order_id": 1, "operation": kwargs.get("operation")}

    registry.register(
        Tool(
            name="execute_trade",
            description="characterization trade",
            parameters={"type": "object", "properties": {"operation": {"type": "string"}}},
            func=execute_trade,
        )
    )
    return registry


@pytest.mark.parametrize(
    ("agent_type", "expected_type"),
    [
        ("react", ReActAgent),
        ("default", ReActAgent),
        ("multi_agent", MultiAgent),
        ("advanced_multi_agent", AdvancedMultiAgent),
        ("rule_aware", RuleAwareAgent),
    ],
)
def test_current_factory_agent_type_mapping(agent_type, expected_type):
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    agent = create_agent(agent_type, llm, ToolRegistry(), max_steps=1, account_id=1)
    assert isinstance(agent, expected_type)
    assert inspect.iscoroutinefunction(agent.run) is False


def test_react_tool_call_is_completed_before_next_llm_step():
    events = []

    class OrderedLLM(FakeLLM):
        def call(self, messages, tools=None):
            events.append(f"llm_{len(self.calls) + 1}")
            if len(self.calls) == 1:
                assert events[-2:] == ["tool_finished", "llm_2"]
            return super().call(messages, tools)

    llm = OrderedLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("call-1", "execute_trade", json.dumps({"operation": "hold"}))],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    agent = create_agent("react", llm, _registry_with_trade(events), max_steps=3)
    agent.set_tool_routing_enabled(False)

    result = agent.run(
        PORTFOLIO,
        PRICES,
        trace_id="trace-1",
        decision_round_id="round-1",
    )

    assert events == ["llm_1", "tool_started", "tool_finished", "llm_2"]
    assert result["protocol"] == "tool"
    assert len(result["executed_trades"]) == 1


def test_react_does_not_repeat_completed_trade_when_trade_done_is_missing():
    events = []
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("call-1", "execute_trade", json.dumps({"operation": "hold"}))],
            ),
            FakeLLMResponse("analysis finished without the legacy marker"),
        ]
    )
    agent = create_agent("react", llm, _registry_with_trade(events), max_steps=2)
    agent.set_tool_routing_enabled(False)

    result = agent.run(
        PORTFOLIO,
        PRICES,
        trace_id="trace-no-marker",
        decision_round_id="round-no-marker",
    )

    assert events == ["tool_started", "tool_finished"]
    assert len(result["executed_trades"]) == 1


def test_fake_llm_can_script_provider_errors():
    llm = FakeLLM([RuntimeError("provider unavailable")])
    with pytest.raises(RuntimeError, match="provider unavailable"):
        llm.call([])


def test_multi_agent_successful_hold_run():
    response = {
        "next_action": "finish",
        "final_decision": {
            "operation": "hold",
            "symbol": "",
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "reason": "done",
        },
    }
    agent = create_agent(
        "multi_agent",
        FakeLLM([FakeLLMResponse(json.dumps(response))]),
        ToolRegistry(),
        max_steps=1,
    )
    result = agent.run(PORTFOLIO, PRICES)
    assert result["operation"] == "hold"
    assert result["reason"].startswith("[MultiAgent]")


def test_advanced_multi_agent_successful_fallback_run():
    agent = create_agent(
        "advanced_multi_agent",
        FakeLLM([FakeLLMResponse("not-json")]),
        ToolRegistry(),
        max_steps=1,
    )
    result = agent.run(PORTFOLIO, PRICES)
    assert result["operation"] == "hold"
    assert result["protocol"] == "tool"
    assert result["executed_trades"] == []


def test_rule_aware_successful_tool_mode_hold(monkeypatch):
    agent = create_agent(
        "rule_aware",
        FakeLLM([FakeLLMResponse("<TRADE_DONE>")]),
        ToolRegistry(),
        max_steps=1,
        account_id=1,
    )
    monkeypatch.setattr(agent, "_attach_compliance_audit", lambda decision, *args: decision)
    result = agent.run(PORTFOLIO, PRICES)
    assert result["operation"] == "hold"
    assert result["protocol"] == "tool"
