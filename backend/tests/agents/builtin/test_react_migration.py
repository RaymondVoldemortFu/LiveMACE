"""Integration tests for the built-in ReAct adapter migration.

These tests never call a real LLM or market API. FakeLLM scripts the model;
tools are in-process callables.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal
from threading import get_ident

import pytest

from benchmark.agents import (
    AgentBuildContext,
    AgentRegistry,
    AgentRuntime,
    AgentRuntimeEvent,
    AgentSelection,
    NullEventSink,
)
from benchmark.builtin.agents import register_builtin_agents
from benchmark.builtin.agents._legacy_context import (
    executed_trades_from_legacy,
    portfolio_from_context,
    prices_from_context,
)
from benchmark.builtin.agents.react import (
    MEMORY_TOOL_NAMES,
    REACT_COMPONENT_ID,
    REACT_VERSION,
    ReActAgentAdapter,
    ReActAgentFactory,
)
from benchmark.builtin.prompts import get_builtin_prompt_registry, render_react_prompt
from benchmark.contracts import (
    ExecutedTradeRef,
    Market,
    PositionView,
    TerminationReason,
)
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from services.agent.factory import create_agent
from services.agent.prompts.system_prompts import _get_trade_agent_prompt_legacy
from services.agent.react import DEFAULT_NON_ROUTED_TOOL_NAMES, ReActAgent
from services.agent.tool_selector import META_TOOL_NAME, REQUIRED_TOOL_NAMES
from services.agent.tools import Tool, ToolRegistry
from tests.agents.builtin.conftest import make_decision_context
from tests.agents.conftest import FakeToolInvoker, RecordingEvents
from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall

FIXED_TIME_TEXT = "2026-08-16 12:00:00"


def _tool_names(openai_tools) -> list[str]:
    return [entry["function"]["name"] for entry in openai_tools or []]


def _register(registry: ToolRegistry, name: str, handler, events: list[str] | None = None):
    def wrapped(**kwargs):
        if events is not None:
            events.append(f"tool:{name}")
        return handler(**kwargs)

    registry.register(
        Tool(
            name=name,
            description=f"test {name}",
            parameters={
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "symbol": {"type": "string"},
                    "market": {"type": "string"},
                    "task": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            func=wrapped,
        )
    )


def _full_tool_registry(
    events: list[str] | None = None,
    trade_result=None,
    *,
    include_memory: bool = True,
) -> ToolRegistry:
    registry = ToolRegistry()
    default_trade = {
        "executed": True,
        "operation": "open",
        "symbol": "BTC",
        "market": "CRYPTO",
        "order_id": 11,
        "trade_id": 12,
    }

    def execute_trade(**kwargs):
        if events is not None:
            events.append("tool:execute_trade")
        result = dict(trade_result or default_trade)
        result.setdefault("operation", kwargs.get("operation", result.get("operation")))
        result.setdefault("symbol", kwargs.get("symbol", result.get("symbol")))
        result.setdefault("market", kwargs.get("market", result.get("market")))
        return result

    _register(registry, "execute_trade", execute_trade)
    names = [
        "get_market_snapshot",
        "get_kline_history",
        "get_account_state",
        "get_history_decisions",
        "consult_search_agent",
        "execute_shell_command",
        "read_file",
        "write_file",
        "run_python_script",
        "memory_search",
        "memory_add",
        "extra_alpha",
        "extra_beta",
    ]
    if not include_memory:
        names = [name for name in names if name not in MEMORY_TOOL_NAMES]
    for name in names:
        _register(registry, name, lambda **kwargs: {"ok": True}, events)
    return registry


def _build_context(llm: FakeLLM, tools: ToolRegistry, events=None) -> AgentBuildContext:
    return AgentBuildContext(
        llm=LegacyLLMClientAdapter(llm),
        tools=tools,
        prompts=get_builtin_prompt_registry(),
        events=events or NullEventSink(),
    )


def _runtime(build_context: AgentBuildContext) -> AgentRuntime:
    registry = AgentRegistry()
    register_builtin_agents(registry)
    registry.freeze()
    return AgentRuntime(registry, build_context)


def _run_react(llm: FakeLLM, tools: ToolRegistry, config: dict, context=None, events=None):
    build = _build_context(llm, tools, events)
    runtime = _runtime(build)
    return runtime.run(
        AgentSelection(REACT_COMPONENT_ID, config=config),
        context or make_decision_context(),
    )


def _freeze_now(monkeypatch):
    monkeypatch.setattr(
        "services.agent.react.now_in_tz",
        lambda tz: datetime(2026, 8, 16, 12, 0, tzinfo=tz),
    )


class _ThreadLLM(FakeLLM):
    def __init__(self, responses, thread_ids):
        super().__init__(responses)
        self._thread_ids = thread_ids

    def call(self, messages, tools=None, **kwargs):
        self._thread_ids.append(get_ident())
        return super().call(messages, tools, **kwargs)


def test_core_react_registers_as_single_component():
    registry = AgentRegistry()
    register_builtin_agents(registry)
    descriptors = [item for item in registry.list() if item.id == REACT_COMPONENT_ID]
    assert len(descriptors) == 1
    assert descriptors[0].version == "1.0.0"


@pytest.mark.parametrize("agent_type", ["react", "default"])
def test_factory_maps_legacy_names_to_core_react(agent_type):
    agent = create_agent(
        agent_type, FakeLLM([FakeLLMResponse("<TRADE_DONE>")]), ToolRegistry(), max_steps=1
    )
    assert isinstance(agent, ReActAgent)
    assert inspect.iscoroutinefunction(agent.run) is False


def test_create_agent_ignores_unrelated_kwargs_for_react():
    agent = create_agent(
        "react",
        FakeLLM([FakeLLMResponse("<TRADE_DONE>")]),
        ToolRegistry(),
        max_steps=1,
        account_id=1,
        enable_llm_audit=True,
    )
    assert isinstance(agent, ReActAgent)
    assert agent.max_steps == 1


def test_adapter_runs_in_caller_thread_and_returns_hold(monkeypatch):
    _freeze_now(monkeypatch)
    thread_ids = []
    llm = _ThreadLLM([FakeLLMResponse("<TRADE_DONE>")], thread_ids)
    events = RecordingEvents()
    result = _run_react(
        llm,
        _full_tool_registry(),
        {"max_steps": 2, "tool_routing_enabled": False, "memory_enabled": False},
        events=events,
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades == ()
    assert result.trace_id == "trace-react"
    assert result.decision_round_id == "round-react"
    assert thread_ids == [get_ident()]
    event_types = [event.type for event in events.events]
    assert event_types[0] == "agent.started"
    assert event_types[-1] == "agent.completed"
    assert "agent.step" in event_types
    assert event_types == (
        ["agent.started"]
        + ["agent.step"] * (len(event_types) - 2)
        + ["agent.completed"]
    )


def test_runtime_accepts_public_tool_invoker_without_legacy_registry(
    monkeypatch, decision_context
):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    build = AgentBuildContext(
        llm=LegacyLLMClientAdapter(llm),
        tools=FakeToolInvoker(),
        prompts=get_builtin_prompt_registry(),
        events=NullEventSink(),
    )
    result = _runtime(build).run(
        AgentSelection(
            REACT_COMPONENT_ID, config={"max_steps": 1, "tool_routing_enabled": False}
        ),
        decision_context,
    )
    assert result.termination_reason is TerminationReason.HOLD


def test_plain_react_exposes_default_non_routed_tools(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    tools = _full_tool_registry()
    _run_react(
        llm,
        tools,
        {"max_steps": 1, "tool_routing_enabled": False, "memory_enabled": True},
    )
    names = _tool_names(llm.calls[0]["tools"])
    expected = [name for name in DEFAULT_NON_ROUTED_TOOL_NAMES if name in tools.tools]
    assert names == sorted(expected)
    assert META_TOOL_NAME not in names


def test_react_tool_starts_with_required_tools_and_selector(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    tools = _full_tool_registry()
    _run_react(
        llm,
        tools,
        {"max_steps": 1, "tool_routing_enabled": True, "memory_enabled": False},
    )
    names = _tool_names(llm.calls[0]["tools"])
    assert names == sorted(REQUIRED_TOOL_NAMES + [META_TOOL_NAME])


def test_react_tool_select_tools_then_hold(monkeypatch):
    _freeze_now(monkeypatch)
    selected = REQUIRED_TOOL_NAMES + ["consult_search_agent", "extra_alpha"]
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("sel-1", META_TOOL_NAME, json.dumps({"task": "choose tools"}))],
            ),
            FakeLLMResponse(json.dumps({"selected_tools": selected})),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    tools = _full_tool_registry()
    result = _run_react(
        llm,
        tools,
        {"max_steps": 4, "tool_routing_enabled": True, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert META_TOOL_NAME in _tool_names(llm.calls[0]["tools"])
    assert llm.calls[1].get("response_format") == {"type": "json_object"}
    assert _tool_names(llm.calls[2]["tools"]) == sorted(selected)


def test_tool_completes_before_next_llm_step_via_runtime(monkeypatch):
    _freeze_now(monkeypatch)
    events = []
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps(
                            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"}
                        ),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(events),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert events == ["tool:execute_trade"]
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert result.executed_trades == (
        ExecutedTradeRef(
            operation="open",
            symbol="BTC",
            market=Market.CRYPTO,
            order_id=11,
            trade_id=12,
            executed=True,
        ),
    )


def test_max_steps_fallback_is_max_steps_not_hold(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("still thinking"), FakeLLMResponse("still thinking")])
    result = _run_react(
        llm,
        _full_tool_registry(),
        {"max_steps": 2, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.MAX_STEPS
    assert result.summary == "max_steps reached, fallback hold"
    assert result.executed_trades == ()


def test_trade_without_termination_token_keeps_executed_trade(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps(
                            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"}
                        ),
                    )
                ],
            ),
            FakeLLMResponse("analysis finished without the legacy marker"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(),
        {"max_steps": 2, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.MAX_STEPS
    assert len(result.executed_trades) == 1
    assert result.executed_trades[0].executed is True


def test_close_all_expands_closed_orders(monkeypatch):
    _freeze_now(monkeypatch)
    trade_result = {
        "executed": True,
        "operation": "close_all",
        "closed_orders": [
            {"symbol": "BTC", "market": "CRYPTO", "order_id": 21},
            {"symbol": "AAPL", "market": "US", "order_id": 22},
        ],
    }
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("call-1", "execute_trade", json.dumps({"operation": "close_all"}))],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(trade_result=trade_result),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert [item.symbol for item in result.executed_trades] == ["BTC", "AAPL"]
    assert [item.market for item in result.executed_trades] == [Market.CRYPTO, Market.US]


def test_rejected_trade_keeps_error_without_inventing_symbol(monkeypatch):
    _freeze_now(monkeypatch)
    trade_result = {"executed": False, "error": "Invalid price for BTC"}
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps({"operation": "open", "symbol": "BTC"}),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(trade_result=trade_result),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades == ()
    assert result.metadata["incomplete_executed_trades"][0]["error"] == "Invalid price for BTC"
    assert result.metadata["trade_errors"][0]["error"] == "Invalid price for BTC"


def test_rejected_trade_with_full_payload_maps_reject_code(monkeypatch):
    _freeze_now(monkeypatch)
    trade_result = {
        "executed": False,
        "operation": "open",
        "symbol": "BTC",
        "market": "CRYPTO",
        "error": "US market is closed",
        "reject_code": "MARKET_CLOSED",
    }
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps(
                            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"}
                        ),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(trade_result=trade_result),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "MARKET_CLOSED"


@pytest.mark.parametrize(
    ("memory_enabled", "tool_routing_enabled"),
    [
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ],
)
def test_prompt_matches_legacy_renderer(monkeypatch, memory_enabled, tool_routing_enabled):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    _run_react(
        llm,
        _full_tool_registry(),
        {
            "max_steps": 1,
            "tool_routing_enabled": tool_routing_enabled,
            "memory_enabled": memory_enabled,
            "include_simulation_notice": False,
        },
    )
    system = llm.calls[0]["messages"][0]["content"]
    expected_prompt = render_react_prompt(
        memory_enabled=memory_enabled,
        tool_routing_enabled=tool_routing_enabled,
        include_simulation_notice=False,
    )
    expected_legacy = _get_trade_agent_prompt_legacy(
        memory_enabled=memory_enabled,
        tool_routing_enabled=tool_routing_enabled,
    )
    assert expected_prompt == expected_legacy
    assert system == f"{expected_prompt}\n\nCurrent Time (UTC+8): {FIXED_TIME_TEXT}"


def _expected_system_prompt(*, memory_enabled: bool, tool_routing_enabled: bool) -> str:
    expected_prompt = render_react_prompt(
        memory_enabled=memory_enabled,
        tool_routing_enabled=tool_routing_enabled,
        include_simulation_notice=False,
    )
    return f"{expected_prompt}\n\nCurrent Time (UTC+8): {FIXED_TIME_TEXT}"


def test_omitted_memory_enabled_follows_registered_memory_tools(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    _run_react(
        llm,
        _full_tool_registry(),
        {
            "max_steps": 1,
            "tool_routing_enabled": False,
            "include_simulation_notice": False,
        },
    )
    assert llm.calls[0]["messages"][0]["content"] == _expected_system_prompt(
        memory_enabled=True,
        tool_routing_enabled=False,
    )


def test_omitted_memory_enabled_without_memory_tools_disables_prompt(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    _run_react(
        llm,
        _full_tool_registry(include_memory=False),
        {
            "max_steps": 1,
            "tool_routing_enabled": False,
            "include_simulation_notice": False,
        },
    )
    assert llm.calls[0]["messages"][0]["content"] == _expected_system_prompt(
        memory_enabled=False,
        tool_routing_enabled=False,
    )


def test_explicit_memory_false_overrides_registered_memory_tools(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM([FakeLLMResponse("<TRADE_DONE>")])
    _run_react(
        llm,
        _full_tool_registry(),
        {
            "max_steps": 1,
            "tool_routing_enabled": False,
            "memory_enabled": False,
            "include_simulation_notice": False,
        },
    )
    assert llm.calls[0]["messages"][0]["content"] == _expected_system_prompt(
        memory_enabled=False,
        tool_routing_enabled=False,
    )


def test_create_agent_omitted_memory_enabled_detects_tools():
    with_memory = create_agent(
        "react",
        FakeLLM([FakeLLMResponse("<TRADE_DONE>")]),
        _full_tool_registry(),
        max_steps=1,
        tool_routing_enabled=False,
    )
    without_memory = create_agent(
        "react",
        FakeLLM([FakeLLMResponse("<TRADE_DONE>")]),
        _full_tool_registry(include_memory=False),
        max_steps=1,
        tool_routing_enabled=False,
    )
    assert with_memory.memory_enabled is True
    assert without_memory.memory_enabled is False


def test_runtime_emits_agent_step_events_in_order(monkeypatch):
    _freeze_now(monkeypatch)
    events = RecordingEvents()
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps(
                            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"}
                        ),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
        events=events,
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    step_events = [event for event in events.events if event.type == "agent.step"]
    assert [event.type for event in events.events] == (
        ["agent.started"] + ["agent.step"] * len(step_events) + ["agent.completed"]
    )
    assert [event.metadata["step_number"] for event in step_events] == list(
        range(1, len(step_events) + 1)
    )
    assert all(event.agent_id == REACT_COMPONENT_ID for event in step_events)
    assert all(event.agent_version == REACT_VERSION for event in step_events)
    assert all(event.trace_id == "trace-react" for event in step_events)
    assert all(event.decision_round_id == "round-react" for event in step_events)
    roles = [event.metadata["role"] for event in step_events]
    assert roles[0] == "assistant"
    assert "tool" in roles
    assert roles[-1] == "assistant"
    first_tool_calls = step_events[0].metadata["tool_calls"]
    assert first_tool_calls[0]["function"]["name"] == "execute_trade"
    tool_event = next(event for event in step_events if event.metadata["role"] == "tool")
    assert tool_event.metadata["name"] == "execute_trade"
    assert tool_event.metadata["tool_call_id"] == "call-1"


def test_agent_step_event_type_is_constructible():
    event = AgentRuntimeEvent(
        type="agent.step",
        agent_id=REACT_COMPONENT_ID,
        agent_version=REACT_VERSION,
        trace_id="trace-react",
        decision_round_id="round-react",
        occurred_at=datetime(2026, 8, 16, 4, 0, tzinfo=timezone.utc),
        metadata={"step_number": 1, "role": "assistant", "content": "<TRADE_DONE>"},
    )
    assert event.type == "agent.step"
    assert dict(event.metadata) == {
        "step_number": 1,
        "role": "assistant",
        "content": "<TRADE_DONE>",
    }


def test_create_agent_and_runtime_are_equivalent_for_plain_react(monkeypatch):
    _freeze_now(monkeypatch)
    script = [
        FakeLLMResponse(
            None,
            [
                FakeToolCall(
                    "call-1",
                    "execute_trade",
                    json.dumps({"operation": "open", "symbol": "BTC", "market": "CRYPTO"}),
                )
            ],
        ),
        FakeLLMResponse("<TRADE_DONE>"),
    ]
    context = make_decision_context()
    portfolio = portfolio_from_context(context)
    prices = prices_from_context(context)

    legacy_llm = FakeLLM(list(script))
    legacy_agent = create_agent(
        "react",
        legacy_llm,
        _full_tool_registry(),
        max_steps=3,
        tool_routing_enabled=False,
        memory_enabled=False,
    )
    legacy_result = legacy_agent.run(
        portfolio,
        prices,
        trace_id=context.trace_id,
        decision_round_id=context.decision_round_id,
    )

    runtime_llm = FakeLLM(list(script))
    runtime_result = _run_react(
        runtime_llm,
        _full_tool_registry(),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
        context=context,
    )

    assert legacy_result["termination_reason"] == "trade_done"
    assert runtime_result.termination_reason is TerminationReason.TRADE_DONE
    assert _tool_names(legacy_llm.calls[0]["tools"]) == _tool_names(runtime_llm.calls[0]["tools"])
    assert len(legacy_llm.calls) == len(runtime_llm.calls)
    assert runtime_result.executed_trades[0].order_id == 11


def test_adapter_last_steps_preserve_on_step_messages(monkeypatch):
    _freeze_now(monkeypatch)
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps({"operation": "open", "symbol": "BTC", "market": "CRYPTO"}),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    tools = _full_tool_registry()
    adapter = ReActAgentFactory().create(
        _build_context(llm, tools),
        {
            "max_steps": 3,
            "tool_routing_enabled": False,
            "memory_enabled": False,
            "step_reminder_threshold": 5,
            "include_simulation_notice": False,
            "agent_name": "demo",
            "user_id": "7",
        },
    )
    assert isinstance(adapter, ReActAgentAdapter)
    result = adapter.run(make_decision_context())
    assert result.termination_reason is TerminationReason.TRADE_DONE
    roles = [step.get("role") for step in adapter.last_steps]
    assert "assistant" in roles
    assert "tool" in roles
    assert result.metadata["step_count"] == len(adapter.last_steps)


def test_portfolio_adapter_matches_production_shape():
    context = make_decision_context(
        positions=(
            PositionView(
                symbol="BTC",
                market=Market.CRYPTO,
                quantity=Decimal("0.5"),
                available_quantity=Decimal("0.5"),
                avg_cost=Decimal("40000"),
                leverage=3,
                side="long",
            ),
            PositionView(
                symbol="ETH",
                market=Market.CRYPTO,
                quantity=Decimal("0"),
                available_quantity=Decimal("0"),
                avg_cost=Decimal("2000"),
                leverage=1,
                side="long",
            ),
        ),
        total_assets="30000",
    )
    portfolio = portfolio_from_context(context)
    assert portfolio == {
        "account_id": 7,
        "cash": 10000.0,
        "frozen_cash": 0.0,
        "positions": {
            "BTC": {
                "quantity": 0.5,
                "avg_cost": 40000.0,
                "current_value": 20000.0,
                "side": "LONG",
                "leverage": 3,
                "market": "CRYPTO",
            }
        },
        "total_assets": 30000.0,
    }
    assert prices_from_context(context) == {"BTC": 50000.0}


def test_executed_trades_helper_does_not_invent_symbol():
    refs, incomplete, errors = executed_trades_from_legacy(
        [{"executed": False, "error": "Account 7 not found"}]
    )
    assert refs == ()
    assert incomplete[0]["error"] == "Account 7 not found"
    assert errors[0]["error"] == "Account 7 not found"


def test_idempotency_fields_still_injected_on_legacy_path(monkeypatch):
    _freeze_now(monkeypatch)
    captured = {}

    def execute_trade(**kwargs):
        captured.update(kwargs)
        return {
            "executed": True,
            "operation": "open",
            "symbol": "BTC",
            "market": "CRYPTO",
            "order_id": 3,
        }

    registry = ToolRegistry()
    _register(registry, "execute_trade", execute_trade)
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "provider-call-9",
                        "execute_trade",
                        json.dumps({"operation": "open", "symbol": "BTC"}),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    agent = create_agent(
        "react",
        llm,
        registry,
        max_steps=3,
        tool_routing_enabled=False,
        memory_enabled=False,
    )
    agent.run(
        portfolio_from_context(make_decision_context()),
        prices_from_context(make_decision_context()),
        trace_id="trace-react",
        decision_round_id="round-react",
    )
    assert captured["decision_round_id"] == "round-react"
    assert captured["tool_call_id"] == "provider-call-9"
    assert "idempotency_key" not in captured


def test_execute_trade_hold_is_hold_not_trade_done(monkeypatch):
    _freeze_now(monkeypatch)
    trade_result = {
        "executed": True,
        "operation": "hold",
        "symbol": "BTC",
        "market": "CRYPTO",
        "message": "No trade executed (hold).",
    }
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-1",
                        "execute_trade",
                        json.dumps({"operation": "hold", "symbol": "BTC", "market": "CRYPTO"}),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(trade_result=trade_result),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades == ()
    assert "incomplete_executed_trades" not in result.metadata


def test_empty_close_all_is_hold_not_trade_done(monkeypatch):
    _freeze_now(monkeypatch)
    trade_result = {
        "executed": True,
        "operation": "close_all",
        "closed_orders": [],
        "message": "No positions to close.",
    }
    llm = FakeLLM(
        [
            FakeLLMResponse(
                None,
                [FakeToolCall("call-1", "execute_trade", json.dumps({"operation": "close_all"}))],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _run_react(
        llm,
        _full_tool_registry(trade_result=trade_result),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.termination_reason is TerminationReason.HOLD
    assert result.executed_trades == ()
    assert "incomplete_executed_trades" not in result.metadata
