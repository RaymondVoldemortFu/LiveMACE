"""Tool runtime error aliases and registry-specific errors."""

from benchmark.contracts import (
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
    ToolRuntimeError,
)


class ToolRegistryFrozenError(ComponentConfigError):
    """Raised when bootstrap code mutates a frozen Tool registry."""

    default_code = "TOOL_REGISTRY_FROZEN"


__all__ = [
    "ComponentConfigError",
    "ComponentConflictError",
    "ComponentNotFoundError",
    "ToolRegistryFrozenError",
    "ToolRuntimeError",
]
