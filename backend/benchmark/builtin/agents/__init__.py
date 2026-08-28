"""Built-in Agent factories registered as core.* components."""

from __future__ import annotations

from benchmark.agents import AgentRegistry
from benchmark.builtin.agents.react import REACT_DESCRIPTOR, ReActAgentFactory
from benchmark.builtin.agents.rule_aware import (
    RULE_AWARE_DESCRIPTOR,
    RuleAwareAgentFactory,
)
from benchmark.contracts import ExtensionRef

BUILTIN_AGENT_EXTENSION = ExtensionRef("benchmark.core", "1.0.0")


def register_builtin_agents(registry: AgentRegistry) -> None:
    """Register built-in Agent factories. Caller freezes the registry."""

    if not isinstance(registry, AgentRegistry):
        raise TypeError("registry must be AgentRegistry")
    registry.register(REACT_DESCRIPTOR, ReActAgentFactory())
    registry.register(RULE_AWARE_DESCRIPTOR, RuleAwareAgentFactory())


__all__ = ["BUILTIN_AGENT_EXTENSION", "register_builtin_agents"]
