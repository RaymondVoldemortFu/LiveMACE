"""Agent framework error aliases and specialized registry errors."""

from benchmark.contracts import (
    AgentRuntimeError,
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
)


class AgentRegistryFrozenError(ComponentConfigError):
    default_code = "AGENT_REGISTRY_FROZEN"


__all__ = [
    "AgentRuntimeError",
    "ComponentConfigError",
    "ComponentConflictError",
    "ComponentNotFoundError",
    "AgentRegistryFrozenError",
]
