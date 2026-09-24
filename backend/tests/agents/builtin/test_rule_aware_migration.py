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
from benchmark.contracts import (
    Market,
    SideEffect,
    TerminationReason,
    ToolResult,
    ToolSpec,
)
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from benchmark.providers import LLMResponse, LLMToolCall
from benchmark.testing import FakeLLMClientPort
from tests.legacy_fixtures.agents import create_agent
from services.agent.llm_client import LLMClient
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
    values = _factory_config(**config)
    return RuleAwareAgentFactory().create(
        _build_context(llm, tools or _trade_registry(), events), values
    )


def _factory_config(**config):
    values = {
        "max_steps": 3,
        "user_id": None,
        "account_id": None,
        "agent_name": None,
        "rule_docs_path": None,
        "enable_llm_audit": False,
    }
    values.update(config)
    return values


def _disable_audit(monkeypatch):
    monkeypatch.setattr(
        RuleAwareAgent,
        "_attach_compliance_audit",
        lambda self, decision, *args, **kwargs: decision,
    )


def test_rule_aware_registers_once_and_legacy_shim_remains():
    registry = AgentRegistry()
    register_builtin_agents(registry)
    descriptors = [
        item for item in registry.list() if item.id == RULE_AWARE_COMPONENT_ID
    ]
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


def test_rule_aware_builds_and_runs_with_public_ports(monkeypatch):
    _disable_audit(monkeypatch)
    llm = FakeLLMClientPort((LLMResponse("<TRADE_DONE>"),))
    tools = FakeToolInvoker()
    context = AgentBuildContext(
        llm=llm,
        tools=tools,
        prompts=get_builtin_prompt_registry(),
        events=NullEventSink(),
    )
    agent = RuleAwareAgentFactory().create(context, _factory_config(max_steps=1))

    result = agent.run(make_decision_context())

    assert result.termination_reason is TerminationReason.HOLD
    assert len(llm.requests) == 1
    assert llm.requests[0].model is None
    assert llm.requests[0].tools == ()
    assert tools.calls == []


def test_rule_aware_never_reads_public_port_model_property(monkeypatch):
    _disable_audit(monkeypatch)

    class ProtectedModelPort:
        def __init__(self):
            self.requests = []
            self.model_reads = 0

        @property
        def model(self):
            self.model_reads += 1
            raise AssertionError("model is not part of LLMClientPort")

        def complete(self, request):
            self.requests.append(request)
            return LLMResponse("<TRADE_DONE>")

    llm = ProtectedModelPort()
    agent = RuleAwareAgentFactory().create(
        AgentBuildContext(
            llm=llm,
            tools=FakeToolInvoker(),
            prompts=get_builtin_prompt_registry(),
            events=NullEventSink(),
        ),
        _factory_config(max_steps=1),
    )

    result = agent.run(make_decision_context())

    assert result.termination_reason is TerminationReason.HOLD
    assert llm.model_reads == 0
    assert [request.model for request in llm.requests] == [None]


def test_rule_aware_uses_public_tool_results_and_explicit_specs(monkeypatch):
    _disable_audit(monkeypatch)

    class PublicTradeInvoker:
        def __init__(self):
            self.calls = []

        def list_specs(self):
            return (
                ToolSpec(
                    name="core.execute_trade",
                    description="test trade",
                    input_schema={"type": "object", "properties": {}},
                    output_schema={"type": "object"},
                    side_effect=SideEffect.TRADING_WRITE,
                ),
            )

        def call(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            return ToolResult(
                ok=True,
                value={
                    "executed": True,
                    "operation": arguments["operation"],
                    "symbol": arguments["symbol"],
                    "market": arguments["market"],
                    "order_id": 7,
                    "trade_id": 8,
                },
            )

    llm = FakeLLMClientPort(
        (
            LLMResponse(
                "",
                tool_calls=(
                    LLMToolCall(
                        id="call-public-1",
                        name="core.execute_trade",
                        arguments={
                            "operation": "open",
                            "symbol": "BTC",
                            "market": "CRYPTO",
                        },
                    ),
                ),
            ),
            LLMResponse("<TRADE_DONE>"),
        )
    )
    tools = PublicTradeInvoker()
    context = AgentBuildContext(
        llm=llm,
        tools=tools,
        prompts=get_builtin_prompt_registry(),
        events=NullEventSink(),
    )
    agent = RuleAwareAgentFactory().create(context, _factory_config(max_steps=2))

    result = agent.run(make_decision_context())

    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert [(trade.symbol, trade.executed) for trade in result.executed_trades] == [
        ("BTC", True)
    ]
    assert tools.calls == [
        (
            "core.execute_trade",
            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"},
        )
    ]
    assert llm.requests[0].tools[0]["function"]["name"] == "execute_trade"
    assert [request.model for request in llm.requests] == [None, None]
    assert llm.requests[1].messages[-1]["role"] == "tool"
    assert llm.requests[1].messages[-1] != LLMClient.gemini_post_tool_user_message()


def test_rule_aware_legacy_gemini_adapter_keeps_continuation(monkeypatch):
    _disable_audit(monkeypatch)

    class GeminiLegacyLLM(FakeLLM):
        def is_gemini_model(self) -> bool:
            return True

    llm = GeminiLegacyLLM(
        [
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-gemini-1",
                        "execute_trade",
                        json.dumps(
                            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"}
                        ),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ],
        model="gemini-2.5-flash",
    )
    adapter = _create_adapter(llm, max_steps=2)

    adapter.run(make_decision_context())

    assert llm.calls[1]["messages"][-1] == LLMClient.gemini_post_tool_user_message()


def test_rule_aware_public_tool_rejection_is_unexecuted(monkeypatch):
    _disable_audit(monkeypatch)

    class RejectingTradeInvoker:
        def __init__(self):
            self.calls = []

        def call(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            return ToolResult(
                ok=False,
                error_code="BROKER_REJECTED",
                error_message="broker rejected order",
            )

    llm = FakeLLMClientPort(
        (
            LLMResponse(
                "",
                tool_calls=(
                    LLMToolCall(
                        id="call-rejected-1",
                        name="core.execute_trade",
                        arguments={
                            "operation": "open",
                            "symbol": "BTC",
                            "market": "CRYPTO",
                        },
                    ),
                ),
            ),
            LLMResponse("<TRADE_DONE>"),
        )
    )
    tools = RejectingTradeInvoker()
    agent = RuleAwareAgentFactory().create(
        AgentBuildContext(
            llm=llm,
            tools=tools,
            prompts=get_builtin_prompt_registry(),
            events=NullEventSink(),
        ),
        _factory_config(max_steps=2),
    )

    result = agent.run(make_decision_context())

    assert result.termination_reason is TerminationReason.HOLD
    assert len(result.executed_trades) == 1
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "BROKER_REJECTED"
    assert result.metadata["trade_errors"][0]["error"] == "broker rejected order"
    assert tools.calls[0][0] == "core.execute_trade"
    assert "decision_round_id" not in tools.calls[0][1]
    assert "tool_call_id" not in tools.calls[0][1]


@pytest.mark.parametrize(
    "argument_name",
    ["idempotency_key", "decision_round_id", "tool_call_id"],
)
def test_rule_aware_preserves_third_party_tool_arguments(monkeypatch, argument_name):
    _disable_audit(monkeypatch)

    class PublicToolInvoker:
        def __init__(self):
            self.calls = []

        def list_specs(self):
            return (
                ToolSpec(
                    name="third_party.echo",
                    description="echo a required argument",
                    input_schema={
                        "type": "object",
                        "properties": {argument_name: {"type": "string"}},
                        "required": [argument_name],
                    },
                    output_schema={"type": "object"},
                    side_effect=SideEffect.READ_ONLY,
                ),
            )

        def call(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            return ToolResult(ok=True, value={"received": dict(arguments)})

    llm = FakeLLMClientPort(
        (
            LLMResponse(
                "",
                tool_calls=(
                    LLMToolCall(
                        id="call-third-party-1",
                        name="third_party.echo",
                        arguments={argument_name: "model-value"},
                    ),
                ),
            ),
            LLMResponse("<TRADE_DONE>"),
        )
    )
    tools = PublicToolInvoker()
    agent = RuleAwareAgentFactory().create(
        AgentBuildContext(
            llm=llm,
            tools=tools,
            prompts=get_builtin_prompt_registry(),
            events=NullEventSink(),
        ),
        _factory_config(max_steps=2),
    )

    agent.run(make_decision_context())

    assert tools.calls == [("third_party.echo", {argument_name: "model-value"})]


def test_rule_aware_legacy_shim_keeps_trade_runtime_ids(monkeypatch):
    _disable_audit(monkeypatch)
    calls = []
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="execute_trade",
            description="legacy trade",
            parameters={"type": "object", "properties": {}},
            func=lambda **arguments: calls.append(arguments)
            or {
                "executed": True,
                "operation": arguments["operation"],
                "symbol": arguments["symbol"],
                "market": arguments["market"],
            },
        )
    )
    adapter = _create_adapter(
        FakeLLM(
            [
                FakeLLMResponse(
                    None,
                    [
                        FakeToolCall(
                            "call-legacy-1",
                            "execute_trade",
                            json.dumps(
                                {
                                    "operation": "open",
                                    "symbol": "BTC",
                                    "market": "CRYPTO",
                                }
                            ),
                        )
                    ],
                ),
                FakeLLMResponse("<TRADE_DONE>"),
            ]
        ),
        tools=registry,
        max_steps=2,
    )

    adapter.run(make_decision_context())

    assert calls[0]["decision_round_id"] == "round-react"
    assert calls[0]["tool_call_id"] == "call-legacy-1"


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
                                {
                                    "operation": "open",
                                    "symbol": "BTC",
                                    "market": "CRYPTO",
                                }
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
                        json.dumps(
                            {"operation": "open", "symbol": "BTC", "market": "CRYPTO"}
                        ),
                    )
                ],
            ),
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "call-2",
                        "execute_trade",
                        json.dumps(
                            {"operation": "open", "symbol": "AAPL", "market": "US"}
                        ),
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
        ["agent.started"] + ["agent.step"] * 5 + ["agent.completed"]
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
                                {
                                    "operation": "open",
                                    "symbol": "BTC",
                                    "market": "CRYPTO",
                                }
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
                                {
                                    "operation": "open",
                                    "symbol": "BTC",
                                    "market": "CRYPTO",
                                }
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

    adapter = _create_adapter(ThreadLLM([FakeLLMResponse("<TRADE_DONE>")]), max_steps=1)
    result = adapter.run(make_decision_context())
    assert result.termination_reason is TerminationReason.HOLD
    assert thread_ids == [get_ident()]
