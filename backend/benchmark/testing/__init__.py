"""Reusable fake provider ports and contract helpers for extension tests."""

from .cache import FakeKlineCache, FakePriceCache, FakeToolCache
from .context import build_fake_build_context, build_fake_context, tool_context_from_decision
from .contracts import (
    AgentCase,
    ContractViolation,
    ToolCase,
    arguments_from_input_schema,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
)
from .providers import (
    FakeLLMClientPort,
    FakeMarketDataPort,
    FakeMemoryStorePort,
    FakeSandboxPort,
    FakeTradeCommandGateway,
)

__all__ = [
    "FakeKlineCache",
    "FakeLLMClientPort",
    "FakeMarketDataPort",
    "FakeMemoryStorePort",
    "FakePriceCache",
    "FakeSandboxPort",
    "FakeToolCache",
    "FakeTradeCommandGateway",
    "AgentCase",
    "ToolCase",
    "ContractViolation",
    "arguments_from_input_schema",
    "assert_agent_contract",
    "assert_prompt_contract",
    "assert_tool_contract",
    "build_fake_context",
    "build_fake_build_context",
    "tool_context_from_decision",
]
