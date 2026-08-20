"""Built-in Agent factories registered as core.* components."""

from __future__ import annotations

from benchmark.agents import AgentRegistry
from benchmark.builtin.agents.advanced_multi_agent import (
    ADVANCED_MULTI_AGENT_DESCRIPTOR,
    AdvancedMultiAgentFactory,
)
from benchmark.builtin.agents.react import REACT_DESCRIPTOR, ReActAgentFactory
from benchmark.contracts import ExtensionRef

BUILTIN_AGENT_EXTENSION = ExtensionRef("benchmark.core", "1.0.0")


def register_builtin_agents(registry: AgentRegistry) -> None:
    """Register built-in Agent factories. Caller freezes the registry."""

    if not isinstance(registry, AgentRegistry):
        raise TypeError("registry must be AgentRegistry")
    registry.register(REACT_DESCRIPTOR, ReActAgentFactory())
    registry.register(ADVANCED_MULTI_AGENT_DESCRIPTOR, AdvancedMultiAgentFactory())


__all__ = ["BUILTIN_AGENT_EXTENSION", "register_builtin_agents"]
