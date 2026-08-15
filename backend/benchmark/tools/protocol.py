"""Stable synchronous Tool SPI and immutable runtime descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from benchmark.contracts import (
    ExtensionRef,
    JsonValue,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from benchmark.contracts.common import _freeze_mapping


@runtime_checkable
class Tool(Protocol):
    """A Tool executes one call synchronously."""

    @property
    def spec(self) -> ToolSpec: ...

    def invoke(
        self,
        context: ToolContext,
        arguments: Mapping[str, JsonValue],
    ) -> ToolResult: ...


@runtime_checkable
class ToolProvider(Protocol):
    """Provides stateless or port-backed Tool instances during bootstrap."""

    def list_tools(self) -> Sequence[Tool]: ...


@runtime_checkable
class ToolInvoker(Protocol):
    """The narrow Tool interface exposed to an Agent."""

    def call(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> ToolResult: ...


@runtime_checkable
class ToolCache(Protocol):
    """Minimal cache port used by the Tool runtime."""

    def get(
        self,
        namespace: str,
        args: dict[str, Any],
        *,
        round_id: str | None = None,
    ) -> object: ...

    def set(
        self,
        namespace: str,
        args: dict[str, Any],
        value: object,
        *,
        ttl_seconds: int | None = None,
        round_id: str | None = None,
    ) -> object: ...


@runtime_checkable
class ToolEventSink(Protocol):
    """Consumes immutable, redacted Tool runtime lifecycle events."""

    def emit(self, event: "ToolRuntimeEvent") -> None: ...


@dataclass(frozen=True)
class RegisteredTool:
    """A Tool and the extension version that registered it."""

    extension: ExtensionRef
    spec: ToolSpec
    tool: Tool = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.extension, ExtensionRef):
            raise TypeError("extension must be ExtensionRef")
        if not isinstance(self.spec, ToolSpec):
            raise TypeError("spec must be ToolSpec")
        if not callable(getattr(self.tool, "invoke", None)):
            raise TypeError("tool must implement invoke(context, arguments)")


@dataclass(frozen=True)
class ToolRuntimeEvent:
    """A redacted lifecycle event emitted by the synchronous invoker."""

    type: str
    account_id: int
    component: ExtensionRef | None
    tool_name: str
    tool_version: str
    trace_id: str
    decision_round_id: str
    call_id: str
    occurred_at: datetime
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in {
            "tool.started",
            "tool.completed",
            "tool.failed",
            "tool.denied",
            "tool.cache_hit",
        }:
            raise ValueError("unsupported Tool runtime event type")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if self.component is not None and not isinstance(self.component, ExtensionRef):
            raise TypeError("component must be ExtensionRef or None")
        for name in (
            "tool_name",
            "tool_version",
            "trace_id",
            "decision_round_id",
            "call_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, "metadata"),
        )


class NullToolEventSink:
    """Default event sink used when tracing is not configured."""

    def emit(self, event: ToolRuntimeEvent) -> None:
        return None


__all__ = [
    "Tool",
    "ToolProvider",
    "ToolInvoker",
    "ToolCache",
    "ToolEventSink",
    "RegisteredTool",
    "ToolRuntimeEvent",
    "NullToolEventSink",
]
