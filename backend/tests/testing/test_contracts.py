"""Public contract helpers reject awaitable Agent/Tool/Prompt implementations."""

from __future__ import annotations

import pytest

from benchmark.contracts import (
    MARKET_READ,
    AgentRunResult,
    PromptSpec,
    SideEffect,
    TerminationReason,
    ToolResult,
    ToolSpec,
)
from benchmark.testing import (
    AgentCase,
    ContractViolation,
    ToolCase,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
    build_fake_context,
)


class _AsyncCreateFactory:
    async def create(self, context, config):
        del context, config
        return object()


class _AsyncRunAgent:
    async def run(self, context):
        del context
        return AgentRunResult(
            trace_id="t",
            decision_round_id="r",
            termination_reason=TerminationReason.HOLD,
        )


class _AsyncRunFactory:
    def create(self, context, config):
        del context, config
        return _AsyncRunAgent()


class _HoldAgent:
    def run(self, context):
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
        )


class _HoldFactory:
    def create(self, context, config):
        del context, config
        return _HoldAgent()


class _AsyncListProvider:
    async def list_tools(self):
        return ()


class _ReadTool:
    def __init__(self):
        self.spec = ToolSpec(
            name="com.example.sync.read",
            description="sync",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            side_effect=SideEffect.READ_ONLY,
            required_capabilities=(MARKET_READ,),
        )

    async def invoke(self, context, arguments):
        del context, arguments
        return ToolResult(ok=True, value={})


class _AsyncInvokeProvider:
    def list_tools(self):
        return (_ReadTool(),)


class _TradingTool:
    def __init__(self):
        self.spec = ToolSpec(
            name="com.example.not-trade",
            description="must not request trading.write",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            side_effect=SideEffect.TRADING_WRITE,
            required_capabilities=("trading.write",),
        )

    def invoke(self, context, arguments):
        del context, arguments
        return ToolResult(ok=True, value={})


class _TradingProvider:
    def list_tools(self):
        return (_TradingTool(),)


class _AsyncPromptProvider:
    def render(self, prompt_id, variables):
        raise AssertionError("render should not be reached")

    async def list_prompts(self):
        return (PromptSpec("com.example.p", "1.0.0", ()),)


class _AsyncRenderPromptProvider:
    def list_prompts(self):
        return (PromptSpec("com.example.p", "1.0.0", ()),)

    async def render(self, prompt_id, variables):
        del prompt_id, variables
        return object()


def test_assert_agent_contract_rejects_awaitable_create():
    with pytest.raises(ContractViolation, match="awaitable"):
        assert_agent_contract(_AsyncCreateFactory(), (AgentCase(name="async-create"),))


def test_assert_agent_contract_rejects_awaitable_run():
    with pytest.raises(ContractViolation, match="awaitable"):
        assert_agent_contract(_AsyncRunFactory(), (AgentCase(name="async-run"),))


def test_assert_agent_contract_accepts_synchronous_hold():
    assert_agent_contract(_HoldFactory(), (AgentCase(name="hold"),))


def test_assert_tool_contract_rejects_awaitable_list_and_invoke():
    with pytest.raises(ContractViolation, match="awaitable"):
        assert_tool_contract(_AsyncListProvider())
    with pytest.raises(ContractViolation, match="awaitable"):
        assert_tool_contract(
            _AsyncInvokeProvider(),
            (ToolCase(name="invoke", tool_name="com.example.sync.read"),),
        )


def test_assert_tool_contract_rejects_trading_write_on_non_execute_trade():
    with pytest.raises(ContractViolation, match="trading.write"):
        assert_tool_contract(_TradingProvider())


def test_assert_prompt_contract_rejects_awaitable_list_and_render():
    with pytest.raises(ContractViolation, match="awaitable"):
        assert_prompt_contract(_AsyncPromptProvider())
    with pytest.raises(ContractViolation, match="awaitable"):
        assert_prompt_contract(_AsyncRenderPromptProvider())


def test_arguments_from_input_schema_covers_required_fields():
    from benchmark.testing import arguments_from_input_schema

    assert arguments_from_input_schema(
        {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["symbol", "limit"],
        }
    ) == {"symbol": "example", "limit": 1}


def test_build_fake_context_is_a_decision_context():
    context = build_fake_context(account_id=4)
    assert context.account_id == 4
    assert context.portfolio.prices["BTC"]
