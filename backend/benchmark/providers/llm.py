"""Synchronous LLM provider port."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Mapping, Protocol, runtime_checkable

from benchmark.contracts import JsonValue, to_jsonable
from benchmark.contracts.common import _freeze_mapping, _require_non_empty

@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.name, "name")
        arguments = to_jsonable(self.arguments)
        if not isinstance(arguments, dict):
            raise TypeError("arguments must be a JSON object")
        object.__setattr__(self, "arguments", _freeze_mapping(arguments, "arguments"))


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
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not isfinite(float(self.temperature))
            or not 0 <= float(self.temperature) <= 2
        ):
            raise ValueError("temperature must be a finite number between 0 and 2")
        if self.max_tokens is not None and (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer")
        messages = to_jsonable(self.messages)
        tools = to_jsonable(self.tools)
        metadata = to_jsonable(self.metadata)
        if not isinstance(messages, list) or not all(
            isinstance(item, dict) for item in messages
        ):
            raise TypeError("messages must contain JSON objects")
        if not isinstance(tools, list) or not all(isinstance(item, dict) for item in tools):
            raise TypeError("tools must contain JSON objects")
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a JSON object")
        object.__setattr__(
            self,
            "messages",
            tuple(_freeze_mapping(item, "message") for item in messages),
        )
        object.__setattr__(
            self,
            "tools",
            tuple(_freeze_mapping(item, "tool") for item in tools),
        )
        object.__setattr__(self, "metadata", _freeze_mapping(metadata, "metadata"))


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


@runtime_checkable
class LLMClientPort(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


__all__ = ["LLMClientPort", "LLMRequest", "LLMResponse", "LLMToolCall"]
