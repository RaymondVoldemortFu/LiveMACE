"""Reusable fake ports, DTO fixtures, and SPI contract assertions.

Everything exported here is persistence-free and safe to use from an
extension's own test suite.
"""

from .cache import FakeKlineCache, FakePriceCache, FakeToolCache
from .context import (
    build_fake_agent_build_context,
    build_fake_build_context,
    build_fake_context,
    tool_context_from_decision,
)
from .contracts import (
    AgentCase,
    ContractViolation,
    ToolCase,
    arguments_from_input_schema,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
)
from .events import FakeEventSink, FakeToolEventSink, RecordingEventSink
from .providers import (
    FakeLLM,
    FakeLLMClientPort,
    FakeMarket,
    FakeMarketData,
    FakeMarketDataPort,
    FakeMemory,
    FakeMemoryStore,
    FakeMemoryStorePort,
    FakeSandbox,
    FakeSandboxPort,
)
from .trade import FakeTradeCommandGateway, FakeTradeGateway

__all__ = [
    "FakeKlineCache",
    "FakeLLM",
    "FakeLLMClientPort",
    "FakeMarket",
    "FakeMarketData",
    "FakeMarketDataPort",
    "FakeMemory",
    "FakeMemoryStore",
    "FakeMemoryStorePort",
    "FakePriceCache",
    "FakeSandbox",
    "FakeSandboxPort",
    "FakeToolCache",
    "FakeEventSink",
    "FakeToolEventSink",
    "RecordingEventSink",
    "FakeTradeCommandGateway",
    "FakeTradeGateway",
    "AgentCase",
    "ToolCase",
    "ContractViolation",
    "arguments_from_input_schema",
    "assert_agent_contract",
    "assert_tool_contract",
    "assert_prompt_contract",
    "build_fake_context",
    "build_fake_agent_build_context",
    "build_fake_build_context",
    "tool_context_from_decision",
]
