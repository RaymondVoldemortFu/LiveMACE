"""Integration tests for the built-in Multi-Agent migration."""

from __future__ import annotations

import json

import pytest

from benchmark.agents import (
    AgentBuildContext,
    AgentRegistry,
    AgentRuntime,
    AgentSelection,
    NullEventSink,
)
from benchmark.builtin.agents import register_builtin_agents
from benchmark.builtin.agents.multi_agent import (
    MULTI_AGENT_COMPONENT_ID,
    MULTI_AGENT_VERSION,
)
from benchmark.builtin.prompts import get_builtin_prompt_registry
from benchmark.contracts import Market, TerminationReason
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from tests.legacy_fixtures.agents import create_agent
from services.agent.multi_agent import MULTI_AGENT_FINAL_TOOL_CALL_ID, MultiAgent
from services.agent.tools import Tool, ToolRegistry
from tests.agents.builtin.conftest import make_decision_context
from tests.agents.conftest import FakeToolInvoker, RecordingEvents
from tests.fakes import FakeLLM, FakeLLMResponse


def _build_context(llm: FakeLLM, tools, events=None) -> AgentBuildContext:
    return AgentBuildContext(
        llm=LegacyLLMClientAdapter(llm),
        tools=tools,
        prompts=get_builtin_prompt_registry(),
        events=events or NullEventSink(),
    )


def _frozen_registry() -> AgentRegistry:
    registry = AgentRegistry()
    register_builtin_agents(registry)
    registry.freeze()
    return registry


def _runtime(llm: FakeLLM, tools: ToolRegistry, events=None) -> AgentRuntime:
    return AgentRuntime(_frozen_registry(), _build_context(llm, tools, events))


def test_multi_agent_registers_as_a_public_component():
    registry = AgentRegistry()
    register_builtin_agents(registry)
    ids = {descriptor.id for descriptor in registry.list()}
    assert ids == {
        "core.advanced-multi-agent",
        "core.multi-agent",
        "core.react",
        "core.rule-aware",
    }
    assert registry.validate_config(MULTI_AGENT_COMPONENT_ID, {}).normalized_config == {
        "max_steps": 15,
        "user_id": None,
        "agent_name": None,
    }
    assert not registry.validate_config(
        MULTI_AGENT_COMPONENT_ID, {"unknown": True}
    ).valid


def test_legacy_factory_name_returns_multi_agent():
    agent = create_agent(
        "multi_agent",
        FakeLLM([FakeLLMResponse("unused")]),
        ToolRegistry(),
        max_steps=2,
    )
    assert isinstance(agent, MultiAgent)
    assert agent.max_steps == 2


def test_legacy_factory_preserves_default_step_limit():
    agent = create_agent(
        "multi_agent",
        FakeLLM([FakeLLMResponse("unused")]),
        ToolRegistry(),
    )
    assert isinstance(agent, MultiAgent)
    assert agent.max_steps == 15


def test_multi_agent_runtime_hold_uses_prompt_resolver_and_step_events():
    llm = FakeLLM(
        [
            FakeLLMResponse(
                json.dumps(
                    {
                        "next_action": "finish",
                        "final_decision": {
                            "operation": "hold",
                            "symbol": "",
                            "direction": "long",
                            "reason": "stay flat",
                        },
                    }
                )
            )
        ]
    )
    events = RecordingEvents()
    result = _runtime(llm, ToolRegistry(), events).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi", decision_round_id="round-multi"
        ),
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades == ()
    assert result.summary == "[MultiAgent] stay flat"
    assert "You are a Hedge Fund Manager" in llm.calls[0]["messages"][0]["content"]
    step_events = [event for event in events.events if event.type == "agent.step"]
    assert step_events
    assert all(event.agent_id == MULTI_AGENT_COMPONENT_ID for event in step_events)
    assert all(event.agent_version == MULTI_AGENT_VERSION for event in step_events)


def test_legacy_multi_agent_run_returns_operation_reason_and_symbol():
    llm = FakeLLM(
        [
            FakeLLMResponse(
                json.dumps(
                    {
                        "next_action": "finish",
                        "final_decision": {
                            "operation": "hold",
                            "reason": "stay flat",
                        },
                    }
                )
            )
        ]
    )
    decision = create_agent("multi_agent", llm, ToolRegistry(), max_steps=2).run(
        {"cash": 10},
        {"BTC": 1},
    )
    assert decision == {
        "operation": "hold",
        "reason": "[MultiAgent] stay flat",
    }


def test_multi_agent_runtime_reports_max_steps_when_manager_never_finishes():
    llm = FakeLLM([FakeLLMResponse("not-json"), FakeLLMResponse("still-not-json")])
    result = _runtime(llm, ToolRegistry()).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(trace_id="trace-max", decision_round_id="round-max"),
    )
    assert result.termination_reason is TerminationReason.MAX_STEPS
    assert result.executed_trades == ()


def test_multi_agent_runtime_accepts_public_tool_invoker():
    llm = FakeLLM([FakeLLMResponse("not-json"), FakeLLMResponse("still-not-json")])
    result = AgentRuntime(
        _frozen_registry(),
        _build_context(llm, FakeToolInvoker()),
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(),
    )
    assert result.termination_reason is TerminationReason.MAX_STEPS


def _open_finish_response(**changes):
    decision = {
        "operation": "open",
        "symbol": "SOL",
        "direction": "long",
        "target_portion_of_balance": 0.23,
        "leverage": 1,
        "reason": "open SOL",
    }
    decision.update(changes)
    return json.dumps({"next_action": "finish", "final_decision": decision})


def _close_finish_response(**changes):
    decision = {
        "operation": "close",
        "symbol": "SOL",
        "direction": "long",
        "target_portion_of_balance": 0.0,
        "leverage": 1,
        "reason": "exit SOL",
    }
    decision.update(changes)
    return json.dumps({"next_action": "finish", "final_decision": decision})


def _register_execute_trade(tools: ToolRegistry, captured: list[dict]) -> None:
    def execute_trade(**kwargs):
        captured.append(dict(kwargs))
        return {
            "executed": True,
            "operation": kwargs.get("operation", "open"),
            "symbol": kwargs.get("symbol", "SOL"),
            "market": kwargs.get("market", "CRYPTO"),
            "order_id": 81,
            "trade_id": 82,
        }

    tools.register(
        Tool(
            name="execute_trade",
            description="execute a test trade",
            parameters={"type": "object", "additionalProperties": True},
            func=execute_trade,
        )
    )


def test_multi_agent_final_open_executes_through_trade_tool():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    events = RecordingEvents()
    result = _runtime(
        FakeLLM([FakeLLMResponse(_open_finish_response())]),
        tools,
        events,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-open", decision_round_id="round-multi-open"
        ),
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert result.metadata["legacy_operation"] == "open"
    assert len(result.executed_trades) == 1
    assert result.executed_trades[0].symbol == "SOL"
    assert result.executed_trades[0].market is Market.CRYPTO
    assert result.executed_trades[0].order_id == 81
    assert result.executed_trades[0].trade_id == 82
    assert result.executed_trades[0].executed is True
    assert captured == [
        {
            "operation": "open",
            "symbol": "SOL",
            "direction": "long",
            "market": "CRYPTO",
            "leverage": 1,
            "target_portion_of_balance": 0.23,
            "reason": "[MultiAgent] open SOL",
            "decision_round_id": "round-multi-open",
            "tool_call_id": MULTI_AGENT_FINAL_TOOL_CALL_ID,
        }
    ]
    step_events = [event for event in events.events if event.type == "agent.step"]
    tool_steps = [
        event for event in step_events if event.metadata.get("name") == "execute_trade"
    ]
    assert tool_steps
    assert tool_steps[0].metadata.get("tool_call_id") == MULTI_AGENT_FINAL_TOOL_CALL_ID


def test_multi_agent_hold_does_not_call_execute_trade():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    result = _runtime(
        FakeLLM(
            [
                FakeLLMResponse(
                    json.dumps(
                        {
                            "next_action": "finish",
                            "final_decision": {
                                "operation": "hold",
                                "symbol": "",
                                "direction": "long",
                                "reason": "stay flat",
                            },
                        }
                    )
                )
            ]
        ),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-hold", decision_round_id="round-multi-hold"
        ),
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades == ()
    assert captured == []


def test_multi_agent_missing_execute_trade_records_unavailable_trade():
    result = _runtime(
        FakeLLM([FakeLLMResponse(_open_finish_response())]),
        ToolRegistry(),
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-missing",
            decision_round_id="round-multi-missing",
        ),
    )
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.metadata["legacy_operation"] == "open"
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "TOOL_UNAVAILABLE"
    assert result.metadata["trade_errors"][0]["error"]


def test_multi_agent_close_all_without_fills_is_trade_done():
    captured: list[dict] = []
    tools = ToolRegistry()

    def execute_trade(**kwargs):
        captured.append(dict(kwargs))
        return {
            "executed": True,
            "operation": "close_all",
            "closed_orders": [],
            "message": "No positions to close.",
        }

    tools.register(
        Tool(
            name="execute_trade",
            description="execute a test trade",
            parameters={"type": "object", "additionalProperties": True},
            func=execute_trade,
        )
    )
    result = _runtime(
        FakeLLM([FakeLLMResponse(_open_finish_response(operation="close_all", symbol=""))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-all",
            decision_round_id="round-multi-close-all",
        ),
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert result.metadata["legacy_operation"] == "close_all"
    assert captured[0]["operation"] == "close_all"


def test_multi_agent_missing_execute_trade_for_close_all_is_tool_error():
    result = _runtime(
        FakeLLM([FakeLLMResponse(_open_finish_response(operation="close_all", symbol=""))]),
        ToolRegistry(),
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-all-missing",
            decision_round_id="round-multi-close-all-missing",
        ),
    )
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.metadata["legacy_operation"] == "close_all"


def test_multi_agent_open_ignores_close_ratio_when_mapping_arguments():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    _runtime(
        FakeLLM([FakeLLMResponse(_open_finish_response(close_ratio=0))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-ratio", decision_round_id="round-multi-ratio"
        ),
    )
    assert "close_ratio" not in captured[0]
    assert captured[0]["target_portion_of_balance"] == 0.23


def test_multi_agent_close_zero_portion_maps_to_full_close_ratio():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    result = _runtime(
        FakeLLM([FakeLLMResponse(_close_finish_response())]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-zero",
            decision_round_id="round-multi-close-zero",
        ),
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert result.metadata["legacy_operation"] == "close"
    assert captured == [
        {
            "operation": "close",
            "symbol": "SOL",
            "direction": "long",
            "market": "CRYPTO",
            "leverage": 1,
            "close_ratio": 1,
            "reason": "[MultiAgent] exit SOL",
            "decision_round_id": "round-multi-close-zero",
            "tool_call_id": MULTI_AGENT_FINAL_TOOL_CALL_ID,
        }
    ]


def test_multi_agent_close_uses_explicit_close_ratio_over_zero_portion():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    _runtime(
        FakeLLM([FakeLLMResponse(_close_finish_response(close_ratio=0.4))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-ratio",
            decision_round_id="round-multi-close-ratio",
        ),
    )
    assert captured[0]["close_ratio"] == 0.4
    assert "target_portion_of_balance" not in captured[0]


def test_multi_agent_close_positive_portion_maps_to_close_ratio():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    _runtime(
        FakeLLM([FakeLLMResponse(_close_finish_response(target_portion_of_balance=0.4))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-portion",
            decision_round_id="round-multi-close-portion",
        ),
    )
    assert captured[0]["close_ratio"] == 0.4
    assert "target_portion_of_balance" not in captured[0]


@pytest.mark.parametrize("close_ratio", [0, 0.0, -1, 1.5, "abc", float("inf")])
def test_multi_agent_invalid_close_ratio_is_rejected_without_calling_trade(close_ratio):
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    result = _runtime(
        FakeLLM([FakeLLMResponse(_close_finish_response(close_ratio=close_ratio))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-invalid",
            decision_round_id="round-multi-close-invalid",
        ),
    )
    assert captured == []
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "SIZING_VALUE_INVALID"


def test_multi_agent_nan_close_ratio_is_rejected_without_calling_trade():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    payload = json.loads(_close_finish_response())
    payload["final_decision"]["close_ratio"] = float("nan")
    result = _runtime(
        FakeLLM([FakeLLMResponse(json.dumps(payload))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-nan",
            decision_round_id="round-multi-close-nan",
        ),
    )
    assert captured == []
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.executed_trades[0].reject_code == "SIZING_VALUE_INVALID"


def test_multi_agent_close_usd_without_amount_is_rejected_without_calling_trade():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    result = _runtime(
        FakeLLM([FakeLLMResponse(_close_finish_response(size_mode="usd"))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-usd-missing",
            decision_round_id="round-multi-close-usd-missing",
        ),
    )
    assert captured == []
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "SIZING_VALUE_INVALID"


@pytest.mark.parametrize("usd_amount", [0, 0.0, -10, "abc", float("nan")])
def test_multi_agent_close_usd_invalid_amount_is_rejected_without_calling_trade(usd_amount):
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    payload = json.loads(_close_finish_response(size_mode="usd"))
    payload["final_decision"]["usd_amount"] = usd_amount
    result = _runtime(
        FakeLLM([FakeLLMResponse(json.dumps(payload))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-usd-invalid",
            decision_round_id="round-multi-close-usd-invalid",
        ),
    )
    assert captured == []
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.executed_trades[0].reject_code == "SIZING_VALUE_INVALID"


def test_multi_agent_close_usd_amount_does_not_inject_close_ratio():
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    _runtime(
        FakeLLM([FakeLLMResponse(_close_finish_response(size_mode="usd", usd_amount=125))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-close-usd",
            decision_round_id="round-multi-close-usd",
        ),
    )
    assert captured[0]["size_mode"] == "usd"
    assert captured[0]["usd_amount"] == 125
    assert "close_ratio" not in captured[0]


def test_multi_agent_tool_input_invalid_error_code_is_tool_error():
    tools = ToolRegistry()

    def execute_trade(**kwargs):
        return {
            "executed": False,
            "error": "leverage is greater than 10",
            "error_code": "TOOL_INPUT_INVALID",
            "operation": kwargs.get("operation", "open"),
            "symbol": kwargs.get("symbol", "SOL"),
            "market": kwargs.get("market", "CRYPTO"),
        }

    tools.register(
        Tool(
            name="execute_trade",
            description="execute a test trade",
            parameters={"type": "object", "additionalProperties": True},
            func=execute_trade,
        )
    )
    result = _runtime(
        FakeLLM([FakeLLMResponse(_open_finish_response(leverage=11))]),
        tools,
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            trace_id="trace-multi-leverage-invalid",
            decision_round_id="round-multi-leverage-invalid",
        ),
    )
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.executed_trades[0].executed is False
    assert result.metadata["trade_errors"][0]["error"]
