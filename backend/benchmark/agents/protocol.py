"""Stable, synchronous Agent SPI and its immutable public descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable
import copy

from benchmark.contracts import (
    AgentRunResult,
    DecisionContext,
    JsonValue,
    ValidationIssue,
    ValidationReport,
    require_identifier,
    require_semver,
)
from benchmark.tools.protocol import ToolInvoker


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw(item) for item in sorted(value, key=repr)]
    return copy.deepcopy(value)


@runtime_checkable
class Agent(Protocol):
    """An Agent completes one decision round synchronously."""

    def run(self, context: DecisionContext) -> AgentRunResult: ...


@runtime_checkable
class AgentFactory(Protocol):
    """Creates a new Agent instance for one account decision worker."""

    def create(
        self,
        context: "AgentBuildContext",
        config: Mapping[str, JsonValue],
    ) -> Agent: ...


@runtime_checkable
class EventSink(Protocol):
    def emit(self, event: "AgentRuntimeEvent") -> None: ...


@dataclass(frozen=True)
class AgentBuildContext:
    """Framework-owned ports available while constructing an Agent."""

    llm: Any
    tools: ToolInvoker
    prompts: Any
    events: EventSink


@dataclass(frozen=True)
class AgentDescriptor:
    id: str
    version: str
    config_schema: Mapping[str, JsonValue] = field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    display_name: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        require_identifier(self.id, "agent id")
        require_semver(self.version, "agent version")
        if not isinstance(self.display_name, str) or not isinstance(
            self.description, str
        ):
            raise TypeError("display_name and description must be strings")
        if not isinstance(self.config_schema, Mapping):
            raise TypeError("config_schema must be a mapping")
        object.__setattr__(self, "config_schema", _freeze(self.config_schema))


@dataclass(frozen=True)
class AgentSelection:
    agent_id: str
    version: str | None = None
    config: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_identifier(self.agent_id, "agent id")
        if self.version is not None:
            require_semver(self.version, "agent version")
        if not isinstance(self.config, Mapping):
            raise TypeError("config must be a mapping")
        object.__setattr__(self, "config", _freeze(self.config))


@dataclass(frozen=True)
class RegisteredAgent:
    descriptor: AgentDescriptor
    factory: AgentFactory


@dataclass(frozen=True)
class AgentRuntimeEvent:
    type: str
    agent_id: str
    agent_version: str
    trace_id: str
    decision_round_id: str
    occurred_at: datetime
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in {
            "agent.started",
            "agent.completed",
            "agent.failed",
            "agent.cancelled",
        }:
            raise ValueError("unsupported Agent runtime event type")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


class NullEventSink:
    def emit(self, event: AgentRuntimeEvent) -> None:
        return None


__all__ = [
    "Agent",
    "AgentFactory",
    "EventSink",
    "AgentBuildContext",
    "AgentDescriptor",
    "AgentSelection",
    "ValidationIssue",
    "ValidationReport",
    "RegisteredAgent",
    "AgentRuntimeEvent",
    "NullEventSink",
]
