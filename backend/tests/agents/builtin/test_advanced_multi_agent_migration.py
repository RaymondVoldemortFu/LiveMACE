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
from benchmark.builtin.prompts import (
    BUILTIN_PROMPT_EXTENSION,
    BUILTIN_PROMPT_INDEX,
    BUILTIN_PROMPT_ROOT,
    get_builtin_prompt_registry,
)
from benchmark.contracts import (
    ComponentConfigError,
    ExtensionRef,
    Market,
    PromptSpec,
    TerminationReason,
)
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter
from benchmark.prompts import (
    PromptRegistry,
    PromptSourcePriority,
    load_prompt_directory,
)
from benchmark.prompts.renderer import parse_template, render_template
from services.agent.factory import create_agent
from services.agent.multi_agent import MultiAgent
from services.agent.multi_agent_advanced import AdvancedMultiAgent
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


class _OverridePromptProvider:
    def __init__(self):
        records = (
            (
                PromptSpec(
                    "core.advanced-multi-agent.manager",
                    "1.0.0",
                    (
                        "objective",
                        "portfolio",
                        "prices",
                        "evidence_book",
                        "context",
                        "conflicts",
                        "collaboration_state",
                    ),
                ),
                (
                    "CUSTOM_DIRECTIVE_ALWAYS_HOLD\n"
                    "objective={objective}\nportfolio={portfolio}\nprices={prices}\n"
                    "evidence={evidence_book}\ncontext={context}\nconflicts={conflicts}\n"
                    "collaboration={collaboration_state}"
                ),
            ),
            (
                PromptSpec(
                    "core.advanced-multi-agent.trading",
                    "1.0.0",
                    ("instruction", "portfolio", "prices"),
                ),
                (
                    "CUSTOM_DIRECTIVE_TRADING_OVERRIDE\n"
                    "instruction={instruction}\nportfolio={portfolio}\nprices={prices}"
                ),
            ),
        )
        self._records = {
            spec.id: (spec, parse_template(template)) for spec, template in records
        }

    def list_prompts(self):
        return tuple(record[0] for record in self._records.values())

    def render(self, prompt_id, variables):
        spec, template = self._records[prompt_id]
        return render_template(template, spec, variables)


def _registry_with_external_advanced_overrides() -> PromptRegistry:
    loaded = load_prompt_directory(BUILTIN_PROMPT_ROOT, BUILTIN_PROMPT_INDEX)
    registry = PromptRegistry()
    registry.register_provider(
        BUILTIN_PROMPT_EXTENSION,
        loaded.provider,
        PromptSourcePriority.BUILTIN,
    )
    registry.register_profiles(
        BUILTIN_PROMPT_EXTENSION,
        loaded.profiles,
        PromptSourcePriority.BUILTIN,
    )
    registry.register_provider(
        ExtensionRef("com.example.prompt-override", "1.0.0"),
        _OverridePromptProvider(),
        PromptSourcePriority.EXTERNAL,
    )
    registry.freeze()
    return registry


def test_only_advanced_multi_agent_registers_as_a_public_component():
    registry = AgentRegistry()
    register_builtin_agents(registry)
    ids = {descriptor.id for descriptor in registry.list()}
    assert ADVANCED_MULTI_AGENT_COMPONENT_ID in ids
    assert "core.multi-agent" not in ids
    assert registry.validate_config(
        ADVANCED_MULTI_AGENT_COMPONENT_ID, {}
    ).normalized_config == {
        "max_steps": 30,
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


def test_advanced_legacy_factory_preserves_default_step_limit():
    agent = create_agent(
        "advanced_multi_agent",
        FakeLLM([FakeLLMResponse("unused")]),
        ToolRegistry(),
    )
    assert isinstance(agent, AdvancedMultiAgent)
    assert agent.max_steps == 30


def test_advanced_runtime_executes_plan_and_returns_trade_ref(monkeypatch):
    monkeypatch.setattr(
        AdvancedMultiAgent, "_notify_evaluator", lambda self, trace_id: None
    )
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
    assert all(
        event.agent_id == ADVANCED_MULTI_AGENT_COMPONENT_ID for event in step_events
    )
    assert all(
        event.agent_version == ADVANCED_MULTI_AGENT_VERSION for event in step_events
    )


def test_incomplete_execution_plan_does_not_report_trade_done(monkeypatch):
    monkeypatch.setattr(
        AdvancedMultiAgent, "_notify_evaluator", lambda self, trace_id: None
    )
    monkeypatch.setattr(AdvancedMultiAgent, "EXECUTION_MAX_STEPS", 2)
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    plan = [
        {
            "operation": "open",
            "symbol": symbol,
            "market": "CRYPTO",
            "direction": "long",
            "target_portion_of_balance": 0.1,
            "leverage": 1,
        }
        for symbol in ("BTC", "ETH")
    ]
    llm = FakeLLM(
        [
            FakeLLMResponse(
                json.dumps(
                    {
                        "next_action": "call_agent",
                        "agent_name": "TradingAgent",
                        "instruction": "Inspect BTC and ETH.",
                        "reason": "required evidence",
                    }
                )
            ),
            FakeLLMResponse(
                json.dumps(
                    {
                        "summary": "Both setups are constructive.",
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
                        "execution_summary": "open BTC then ETH",
                        "execution_plan": plan,
                    }
                )
            ),
            FakeLLMResponse(
                None,
                [
                    FakeToolCall(
                        "trade-1",
                        "execute_trade",
                        json.dumps(plan[0]),
                    )
                ],
            ),
            FakeLLMResponse("<TRADE_DONE>"),
        ]
    )
    result = _runtime(llm, tools).run(
        AgentSelection(ADVANCED_MULTI_AGENT_COMPONENT_ID, config={"max_steps": 3}),
        make_decision_context(
            trace_id="trace-incomplete", decision_round_id="round-incomplete"
        ),
    )
    assert result.termination_reason is TerminationReason.MAX_STEPS
    assert len(result.executed_trades) == 1
    assert result.metadata["execution_complete"] is False
    assert result.metadata["completed_execution_calls"] == 1
    assert result.metadata["expected_execution_calls"] == 2
    assert len(captured) == 1


def test_advanced_builtin_prompt_messages_preserve_legacy_role_partition(monkeypatch):
    agent = AdvancedMultiAgent(FakeLLM([]), ToolRegistry())
    monkeypatch.setattr(agent, "_current_time_context", lambda: "TIME-CONTEXT")
    monkeypatch.setattr(agent, "_tradable_universe_context", lambda: "UNIVERSE-CONTEXT")
    variables = {
        "objective": "objective",
        "portfolio": json.dumps({"cash": 10}, ensure_ascii=False),
        "prices": json.dumps({"BTC": 1}, ensure_ascii=False),
        "evidence_book": agent._format_evidence_book(),
        "context": "No prior actions.",
        "conflicts": agent._format_conflicts(),
        "collaboration_state": agent._format_collaboration_state(0),
    }
    messages = agent._build_manager_messages(
        objective="objective",
        context_str="No prior actions.",
        portfolio={"cash": 10},
        prices={"BTC": 1},
        step=0,
    )
    rendered_manager = agent.prompt_resolver.render_slot(
        agent.PROMPT_PROFILE_ID,
        "manager",
        variables,
    ).content.strip()
    manager_intro = rendered_manager.split("Trading objective:", 1)[0].strip()
    manager_schema = rendered_manager.split("Decision Protocol:", 1)[1].strip()
    expected_manager_system = (
        f"{manager_intro}\n\nDecision Protocol:\n{manager_schema}"
    ).strip()
    expected_manager_user = (
        "Current trading task state:\n"
        "Trading objective:\nobjective\n\n"
        f"Portfolio:\n{json.dumps({'cash': 10}, ensure_ascii=False)}\n\n"
        f"Market Prices:\n{json.dumps({'BTC': 1}, ensure_ascii=False)}\n\n"
        "TIME-CONTEXT\n\nUNIVERSE-CONTEXT\n\n"
        f"Evidence Book (use evidence IDs when citing prior findings):\n{variables['evidence_book']}\n\n"
        "Current Context:\nNo prior actions.\n\n"
        f"Known Conflicts/Tensions:\n{variables['conflicts']}\n\n"
        f"Collaboration State:\n{agent._format_collaboration_state(0)}\n\n"
        "Decide the next action now and return ONLY JSON."
    )
    assert messages == [
        {"role": "system", "content": expected_manager_system},
        {"role": "user", "content": expected_manager_user},
    ]

    trading_variables = {
        "instruction": "Inspect BTC.",
        "portfolio": json.dumps({"cash": 10}, ensure_ascii=False),
        "prices": json.dumps({"BTC": 1}, ensure_ascii=False),
    }
    rendered_trading = agent.prompt_resolver.render_slot(
        agent.PROMPT_PROFILE_ID,
        "trading",
        trading_variables,
    ).content
    trading_messages = agent._build_sub_agent_messages(
        "TradingAgent",
        rendered_trading,
        "Inspect BTC.",
        {"cash": 10},
        {"BTC": 1},
    )
    trading_intro = rendered_trading.split("Instruction:", 1)[0].strip()
    trading_schema = rendered_trading.split("Return ONLY JSON:", 1)[1].strip()
    assert trading_messages[0]["content"] == (
        f"{trading_intro}\n\nReturn ONLY JSON:\n{trading_schema}"
    ).strip()
    assert trading_messages[1]["content"] == (
        "Current task for TradingAgent:\nInspect BTC.\n\nTIME-CONTEXT\n\n"
        "UNIVERSE-CONTEXT\n\n"
        f"Portfolio:\n{json.dumps({'cash': 10}, ensure_ascii=False)}\n\n"
        f"Prices:\n{json.dumps({'BTC': 1}, ensure_ascii=False)}\n\nRespond now."
    )


def test_advanced_external_prompt_overrides_are_opaque_and_not_duplicated(monkeypatch):
    agent = AdvancedMultiAgent(
        FakeLLM([]),
        ToolRegistry(),
        prompt_resolver=_registry_with_external_advanced_overrides(),
    )
    monkeypatch.setattr(agent, "_current_time_context", lambda: "TIME-CONTEXT")
    monkeypatch.setattr(agent, "_tradable_universe_context", lambda: "UNIVERSE-CONTEXT")
    manager_messages = agent._build_manager_messages(
        objective="OBJECTIVE-UNIQUE",
        context_str="No prior actions.",
        portfolio={"cash": 10},
        prices={"BTC": 1},
        step=0,
    )
    trading_prompt = agent.prompt_resolver.render_slot(
        agent.PROMPT_PROFILE_ID,
        "trading",
        {
            "instruction": "INSTRUCTION-UNIQUE",
            "portfolio": json.dumps({"cash": 10}, ensure_ascii=False),
            "prices": json.dumps({"BTC": 1}, ensure_ascii=False),
        },
    ).content
    trading_messages = agent._build_sub_agent_messages(
        agent_name="TradingAgent",
        prompt_template=trading_prompt,
        instruction="INSTRUCTION-UNIQUE",
        portfolio={"cash": 10},
        prices={"BTC": 1},
    )
    assert "CUSTOM_DIRECTIVE_ALWAYS_HOLD" in manager_messages[0]["content"]
    assert "CUSTOM_DIRECTIVE_TRADING_OVERRIDE" in trading_messages[0]["content"]
    manager_combined = "\n".join(message["content"] for message in manager_messages)
    trading_combined = "\n".join(message["content"] for message in trading_messages)
    assert manager_combined.count("OBJECTIVE-UNIQUE") == 1
    assert manager_combined.count(json.dumps({"cash": 10}, ensure_ascii=False)) == 1
    assert trading_combined.count("INSTRUCTION-UNIQUE") == 1
    assert trading_combined.count(json.dumps({"BTC": 1}, ensure_ascii=False)) == 1


def test_execution_rejects_duplicate_call_for_previous_plan_item(monkeypatch):
    monkeypatch.setattr(AdvancedMultiAgent, "EXECUTION_MAX_STEPS", 2)
    captured: list[dict] = []
    tools = ToolRegistry()
    _register_execute_trade(tools, captured)
    plan = [
        {
            "operation": "open",
            "symbol": symbol,
            "market": "CRYPTO",
            "direction": "long",
            "target_portion_of_balance": 0.1,
            "leverage": 1,
        }
        for symbol in ("BTC", "ETH")
    ]
    duplicate_btc = FakeToolCall(
        "trade-duplicate",
        "execute_trade",
        json.dumps(plan[0]),
    )
    agent = AdvancedMultiAgent(
        FakeLLM(
            [
                FakeLLMResponse(None, [FakeToolCall("trade-1", "execute_trade", json.dumps(plan[0]))]),
                FakeLLMResponse(None, [duplicate_btc]),
            ]
        ),
        tools,
    )

    result = agent._run_execution_stage(
        execution_plan=plan,
        decision={},
        portfolio={},
        prices={},
        decision_round_id="round-duplicate",
    )

    assert result.complete is False
    assert result.matched_plan_items == 1
    assert result.expected_plan_items == 2
    assert len(result.trades) == 1
    assert [call["symbol"] for call in captured] == ["BTC"]


def test_execution_plan_matching_requires_operation_symbol_and_market():
    agent = AdvancedMultiAgent(FakeLLM([]), ToolRegistry())
    plan_item = {
        "operation": "open",
        "symbol": "BTC",
        "market": "CRYPTO",
    }

    assert agent._execution_call_matches_plan_item(
        plan_item,
        {"operation": "OPEN", "symbol": "btc", "market": "CRYPTO"},
    )
    assert not agent._execution_call_matches_plan_item(
        plan_item,
        {"operation": "close", "symbol": "BTC", "market": "CRYPTO"},
    )
    assert not agent._execution_call_matches_plan_item(
        plan_item,
        {"operation": "open", "symbol": "ETH", "market": "CRYPTO"},
    )
    assert not agent._execution_call_matches_plan_item(
        plan_item,
        {"operation": "open", "symbol": "BTC", "market": "US"},
    )


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
