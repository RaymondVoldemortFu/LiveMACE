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
from benchmark.contracts import TerminationReason
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from services.agent.factory import create_agent
from services.agent.multi_agent import MultiAgent
from services.agent.tools import ToolRegistry
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
