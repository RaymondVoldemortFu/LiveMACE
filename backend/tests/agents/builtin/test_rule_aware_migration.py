from __future__ import annotations

import json
from threading import get_ident

import pytest

from benchmark.agents import (
    AgentBuildContext,
    AgentRegistry,
    AgentRuntime,
    AgentSelection,
    NullEventSink,
)
from benchmark.builtin.agents import register_builtin_agents
from benchmark.builtin.agents._legacy_context import nested_executed_trades_from_legacy
from benchmark.builtin.agents.rule_aware import (
    RULE_AWARE_COMPONENT_ID,
    RULE_AWARE_VERSION,
    RuleAwareAgentFactory,
)
from benchmark.builtin.prompts import get_builtin_prompt_registry
from benchmark.contracts import ComponentConfigError, Market, TerminationReason
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from services.agent.factory import create_agent
from services.agent.rule_aware.rule_aware_agent import RuleAwareAgent
from services.agent.tools import Tool, ToolRegistry
from tests.agents.builtin.conftest import make_decision_context
from tests.agents.conftest import FakeToolInvoker, RecordingEvents
from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall


def _build_context(llm, tools, events=None):
    return AgentBuildContext(
        llm=LegacyLLMClientAdapter(llm),
        tools=tools,
        prompts=get_builtin_prompt_registry(),
        events=events or NullEventSink(),
    )


def _runtime(context):
    registry = AgentRegistry()
    register_builtin_agents(registry)
    registry.freeze()
    return AgentRuntime(registry, context)


def _trade_registry():
    registry = ToolRegistry()
    counter = {"value": 0}

    def execute_trade(**kwargs):
        counter["value"] += 1
        return {
            "executed": True,
            "operation": kwargs.get("operation"),
            "symbol": kwargs.get("symbol"),
            "market": kwargs.get("market"),
            "order_id": counter["value"],
            "trade_id": counter["value"] + 100,
        }

    registry.register(
        Tool(
            name="execute_trade",
            description="test trade",
            parameters={"type": "object", "properties": {}},
            func=execute_trade,
        )
    )
    return registry


def _create_adapter(llm, tools=None, events=None, **config):
    values = {
        "max_steps": 3,
        "user_id": None,
        "account_id": None,
        "agent_name": None,
        "rule_docs_path": None,
        "enable_llm_audit": False,
    }
    values.update(config)
    return RuleAwareAgentFactory().create(
        _build_context(llm, tools or _trade_registry(), events), values
    )


def _disable_audit(monkeypatch):
    monkeypatch.setattr(
        RuleAwareAgent,
        "_attach_compliance_audit",
        lambda self, decision, *args, **kwargs: decision,
    )


def test_rule_aware_registers_once_and_legacy_shim_remains():
    registry = AgentRegistry()
    register_builtin_agents(registry)
    descriptors = [item for item in registry.list() if item.id == RULE_AWARE_COMPONENT_ID]
    assert [(item.id, item.version) for item in descriptors] == [
        (RULE_AWARE_COMPONENT_ID, RULE_AWARE_VERSION)
    ]

    agent = create_agent(
        "rule_aware",
        FakeLLM([FakeLLMResponse("<TRADE_DONE>")]),
        ToolRegistry(),
        max_steps=1,
        account_id=1,
        legacy_unused_option=True,
    )
    assert isinstance(agent, RuleAwareAgent)


def test_rule_aware_rejects_public_tool_invoker_only():
    context = _build_context(
        FakeLLM([FakeLLMResponse("<TRADE_DONE>")]),
        FakeToolInvoker(),
    )
    with pytest.raises(ComponentConfigError) as caught:
        RuleAwareAgentFactory().create(
            context,
            {
                "max_steps": 1,
                "user_id": None,
                "account_id": None,
                "agent_name": None,
                "rule_docs_path": None,
                "enable_llm_audit": False,
            },
        )
    assert caught.value.code == "RULE_AWARE_LEGACY_TOOL_REGISTRY_REQUIRED"


def test_nested_trade_conversion_preserves_order_and_no_defaults():
    refs, incomplete, errors = nested_executed_trades_from_legacy(
        [
            {
                "args": {"operation": "open", "symbol": "BTC", "market": "CRYPTO"},
                "result": {"executed": True, "order_id": 1},
            },
            {
                "args": {"operation": "open"},
                "result": {"executed": False, "error": "missing market"},
            },
            {
                "args": {"operation": "hold", "symbol": "ETH", "market": "CRYPTO"},
                "result": {"executed": True},
            },
        ]
    )
    assert [(item.symbol, item.market, item.order_id) for item in refs] == [
        ("BTC", Market.CRYPTO, 1)
    ]
    assert incomplete[0]["args"]["operation"] == "open"
    assert errors[0]["error"] == "missing market"


@pytest.mark.parametrize(
    "result",
    [
        {"error": "Tool execution failed: broker unavailable"},
        {"operation": "open", "symbol": "BTC", "market": "CRYPTO"},
        {
            "executed": True,
            "operation": "open",
            "symbol": "BTC",
            "market": "CRYPTO",
            "error": "broker reported an inconsistent result",
        },
    ],
)
def test_nested_trade_conversion_requires_explicit_error_free_execution(result):
    refs, incomplete, _ = nested_executed_trades_from_legacy(
        [
            {
                "args": {"operation": "open", "symbol": "BTC", "market": "CRYPTO"},
                "result": result,
            }
        ]
    )
    assert incomplete == ()
    assert len(refs) == 1
    assert refs[0].executed is False


def test_rule_aware_tool_exception_is_reported_as_unexecuted(monkeypatch):
    _disable_audit(monkeypatch)

    def execute_trade(**kwargs):
        raise RuntimeError("broker unavailable")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="execute_trade",
            description="failing trade",
            parameters={"type": "object", "properties": {}},
            func=execute_trade,
        )
    )
    adapter = _create_adapter(
        FakeLLM(
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
        ),
        tools=registry,
    )
    result = adapter.run(make_decision_context())

    assert result.termination_reason is TerminationReason.HOLD
    assert len(result.executed_trades) == 1
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "TRADE_REJECTED"
    assert result.metadata["trade_errors"][0]["error"].endswith("broker unavailable")


def test_rule_aware_runtime_emits_steps_and_preserves_two_trades(monkeypatch):
    _disable_audit(monkeypatch)
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
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-2",
                        "execute_trade",
                        json.dumps({"operation": "open", "symbol": "AAPL", "market": "US"}),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    events = RecordingEvents()
    result = _runtime(_build_context(llm, _trade_registry(), events)).run(
        AgentSelection(RULE_AWARE_COMPONENT_ID, config={"max_steps": 4}),
        make_decision_context(),
    )
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert [item.symbol for item in result.executed_trades] == ["BTC", "AAPL"]
    assert [event.type for event in events.events] == (
        ["agent.started"]
        + ["agent.step"] * 5
        + ["agent.completed"]
    )
    steps = [event for event in events.events if event.type == "agent.step"]
    assert [event.metadata["step_number"] for event in steps] == [1, 2, 3, 4, 5]
    assert steps[1].metadata["tool_call_id"] == "call-1"


def test_rule_aware_max_steps_keeps_previous_trade(monkeypatch):
    _disable_audit(monkeypatch)
    adapter = _create_adapter(
        FakeLLM(
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
                FakeLLMResponse("still reasoning"),
            ]
        ),
        max_steps=2,
    )
    result = adapter.run(make_decision_context())
    assert result.termination_reason is TerminationReason.MAX_STEPS
    assert len(result.executed_trades) == 1
    assert result.executed_trades[0].symbol == "BTC"


def test_rule_aware_llm_error_keeps_previous_trade(monkeypatch):
    _disable_audit(monkeypatch)
    adapter = _create_adapter(
        FakeLLM(
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
                RuntimeError("provider unavailable"),
            ]
        ),
        max_steps=3,
    )
    result = adapter.run(make_decision_context())
    assert result.termination_reason is TerminationReason.LLM_ERROR
    assert len(result.executed_trades) == 1


def test_rule_aware_adapter_runs_in_calling_thread(monkeypatch):
    _disable_audit(monkeypatch)
    thread_ids = []

    class ThreadLLM(FakeLLM):
        def call(self, messages, tools=None, **kwargs):
            thread_ids.append(get_ident())
            return super().call(messages, tools, **kwargs)

    adapter = _create_adapter(
        ThreadLLM([FakeLLMResponse("<TRADE_DONE>")]), max_steps=1
    )
    result = adapter.run(make_decision_context())
    assert result.termination_reason is TerminationReason.HOLD
    assert thread_ids == [get_ident()]
