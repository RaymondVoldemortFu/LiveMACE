"""Integration tests for the built-in Advanced Multi-Agent migration."""

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
from benchmark.builtin.agents.advanced_multi_agent import (
    ADVANCED_MULTI_AGENT_COMPONENT_ID,
    ADVANCED_MULTI_AGENT_VERSION,
)
from benchmark.builtin.prompts import get_builtin_prompt_registry
from benchmark.contracts import ComponentConfigError, Market, TerminationReason
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from services.agent.factory import create_agent
from services.agent.multi_agent import MultiAgent
from services.agent.multi_agent_advanced import AdvancedMultiAgent
from services.agent.prompts.advanced_multi_agent_prompts import Advanced_MANAGER_PROMPT
from services.agent.tools import Tool, ToolRegistry
from tests.agents.builtin.conftest import make_decision_context
from tests.agents.conftest import FakeToolInvoker, RecordingEvents
from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall


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


def _register_execute_trade(tools: ToolRegistry, captured: list[dict]) -> None:
    def execute_trade(**kwargs):
        captured.append(dict(kwargs))
        return {
            "executed": True,
            "operation": kwargs.get("operation", "open"),
            "symbol": kwargs.get("symbol", "BTC"),
            "market": kwargs.get("market", "CRYPTO"),
            "order_id": 41,
            "trade_id": 42,
        }

    tools.register(
        Tool(
            name="execute_trade",
            description="execute a test trade",
            parameters={"type": "object", "additionalProperties": True},
            func=execute_trade,
        )
    )


def test_only_advanced_multi_agent_registers_as_a_public_component():
    registry = AgentRegistry()
    register_builtin_agents(registry)
    ids = {descriptor.id for descriptor in registry.list()}
    assert ADVANCED_MULTI_AGENT_COMPONENT_ID in ids
    assert "core.multi-agent" not in ids
    assert registry.validate_config(
        ADVANCED_MULTI_AGENT_COMPONENT_ID, {}
    ).normalized_config == {
        "max_steps": 15,
        "user_id": None,
        "agent_name": None,
    }
    assert not registry.validate_config(
        ADVANCED_MULTI_AGENT_COMPONENT_ID, {"unknown": True}
    ).valid


def test_basic_multi_agent_remains_on_the_legacy_factory_path():
    agent = create_agent(
        "multi_agent",
        FakeLLM([FakeLLMResponse("unused")]),
        ToolRegistry(),
        max_steps=2,
    )
    assert isinstance(agent, MultiAgent)
    assert agent.max_steps == 2


def test_advanced_legacy_factory_name_returns_advanced_agent():
    agent = create_agent(
        "advanced_multi_agent",
        FakeLLM([FakeLLMResponse("unused")]),
        ToolRegistry(),
        max_steps=2,
        account_id=7,
    )
    assert isinstance(agent, AdvancedMultiAgent)
    assert agent.max_steps == 2


def test_advanced_runtime_executes_plan_and_returns_trade_ref(monkeypatch):
    monkeypatch.setattr(AdvancedMultiAgent, "_notify_evaluator", lambda self, trace_id: None)
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    llm = FakeLLM(
        [
            FakeLLMResponse(
                json.dumps(
                    {
                        "next_action": "call_agent",
                        "agent_name": "TradingAgent",
                        "instruction": "Inspect BTC.",
                        "reason": "required evidence",
                    }
                )
            ),
            FakeLLMResponse(
                json.dumps(
                    {
                        "summary": "BTC setup is constructive.",
                        "recommendations": [],
                        "risks": [],
                    }
                )
            ),
            FakeLLMResponse(
                json.dumps(
                    {
                        "next_action": "finish",
                        "reason": "evidence reviewed",
                        "execution_summary": "open a small BTC position",
                        "execution_plan": [
                            {
                                "operation": "open",
                                "symbol": "BTC",
                                "market": "CRYPTO",
                                "direction": "long",
                                "target_portion_of_balance": 0.1,
                                "leverage": 1,
                            }
                        ],
                    }
                )
            ),
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "trade-1",
                        "execute_trade",
                        json.dumps(
                            {
                                "operation": "open",
                                "symbol": "BTC",
                                "market": "CRYPTO",
                                "direction": "long",
                                "target_portion_of_balance": 0.1,
                                "leverage": 1,
                            }
                        ),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    events = RecordingEvents()
    result = _runtime(llm, tools, events).run(
        AgentSelection(ADVANCED_MULTI_AGENT_COMPONENT_ID, config={"max_steps": 3}),
        make_decision_context(
            trace_id="trace-advanced", decision_round_id="round-advanced"
        ),
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert len(result.executed_trades) == 1
    assert result.executed_trades[0].market is Market.CRYPTO
    assert result.executed_trades[0].order_id == 41
    assert captured[0]["tool_call_id"] == "trade-1"
    assert captured[0]["decision_round_id"] == "round-advanced"
    step_events = [event for event in events.events if event.type == "agent.step"]
    assert all(event.agent_id == ADVANCED_MULTI_AGENT_COMPONENT_ID for event in step_events)
    assert all(event.agent_version == ADVANCED_MULTI_AGENT_VERSION for event in step_events)


def test_advanced_manager_system_prompt_preserves_legacy_text():
    agent = AdvancedMultiAgent(FakeLLM([]), ToolRegistry())
    messages = agent._build_manager_messages(
        objective="objective",
        context_str="No prior actions.",
        portfolio={"cash": 10},
        prices={"BTC": 1},
        step=0,
    )
    intro, _ = agent._safe_split_once(Advanced_MANAGER_PROMPT, "Trading objective:")
    _, protocol = agent._safe_split_once(Advanced_MANAGER_PROMPT, "Decision Protocol:")
    expected = "\n\n".join([intro, f"Decision Protocol:\n{protocol}"]).strip()
    assert messages[0]["content"] == expected


def test_advanced_runtime_rejects_public_tool_invoker():
    runtime = AgentRuntime(
        _frozen_registry(),
        _build_context(FakeLLM([]), FakeToolInvoker()),
    )
    with pytest.raises(ComponentConfigError) as caught:
        runtime.run(
            AgentSelection(ADVANCED_MULTI_AGENT_COMPONENT_ID),
            make_decision_context(),
        )
    assert caught.value.code == "ADVANCED_MULTI_AGENT_LEGACY_TOOL_REGISTRY_REQUIRED"
