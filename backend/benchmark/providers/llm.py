"""Synchronous LLM provider port."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from benchmark.contracts import JsonValue
from benchmark.contracts.common import _freeze_mapping, _require_non_empty

from .health import HealthStatus


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments, "arguments"))


@dataclass(frozen=True)
class LLMRequest:
    messages: tuple[Mapping[str, JsonValue], ...]
    model: str
    tools: tuple[Mapping[str, JsonValue], ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.messages, tuple):
            raise TypeError("messages must be a tuple")
        _require_non_empty(self.model, "model")
        if not isinstance(self.tools, tuple):
            raise TypeError("tools must be a tuple")
        object.__setattr__(self, "messages", tuple(_freeze_mapping(item, "message") for item in self.messages))
        object.__setattr__(self, "tools", tuple(_freeze_mapping(item, "tool") for item in self.tools))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[LLMToolCall, ...] = ()
    finish_reason: str | None = None
    raw: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("content must be str")
        if not isinstance(self.tool_calls, tuple) or not all(isinstance(item, LLMToolCall) for item in self.tool_calls):
            raise TypeError("tool_calls must be a tuple of LLMToolCall")
        object.__setattr__(self, "raw", _freeze_mapping(self.raw, "raw"))


class LLMClientPort(Protocol):
    id: str
    version: str
    capabilities: tuple[str, ...]
    config_schema: Mapping[str, JsonValue]

    def complete(self, request: LLMRequest) -> LLMResponse: ...
    def healthcheck(self) -> HealthStatus: ...


__all__ = ["LLMClientPort", "LLMRequest", "LLMResponse", "LLMToolCall"]
