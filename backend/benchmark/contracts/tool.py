"""Public benchmark Tool description, context, and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping

from .common import JsonValue, _freeze_mapping, _require_non_empty


class SideEffect(str, Enum):
    READ_ONLY = "read_only"
    EXTERNAL_READ = "external_read"
    MEMORY_WRITE = "memory_write"
    SANDBOX_WRITE = "sandbox_write"
    TRADING_WRITE = "trading_write"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, JsonValue]
    output_schema: Mapping[str, JsonValue]
    side_effect: SideEffect
    timeout_seconds: float = 30.0
    cacheable: bool = False
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.description, "description")
        if "." not in self.name:
            raise ValueError("tool name must use a namespace")
        object.__setattr__(self, "input_schema", _freeze_mapping(self.input_schema, "input_schema"))
        object.__setattr__(self, "output_schema", _freeze_mapping(self.output_schema, "output_schema"))
        if not isinstance(self.side_effect, SideEffect):
            raise TypeError("side_effect must be SideEffect")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(self.cacheable, bool):
            raise TypeError("cacheable must be bool")
        if not isinstance(self.required_capabilities, tuple):
            raise TypeError("required_capabilities must be a tuple")
        for capability in self.required_capabilities:
            _require_non_empty(capability, "capability")


@dataclass(frozen=True)
class ToolContext:
    account_id: int
    decision_round_id: str
    trace_id: str
    call_id: str
    capabilities: frozenset[str]
    deadline_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        for name in ("decision_round_id", "trace_id", "call_id"):
            _require_non_empty(getattr(self, name), name)
        if not isinstance(self.capabilities, frozenset):
            raise TypeError("capabilities must be frozenset")
        if not isinstance(self.deadline_at, datetime):
            raise TypeError("deadline_at must be datetime")
        if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
            raise ValueError("deadline_at must be timezone-aware")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    value: JsonValue = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool) or not isinstance(self.retryable, bool):
            raise TypeError("ok and retryable must be bool")
        if self.ok and (self.error_code is not None or self.error_message is not None):
            raise ValueError("successful ToolResult cannot contain an error")
        if not self.ok and not self.error_code:
            raise ValueError("failed ToolResult requires error_code")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))


__all__ = ["SideEffect", "ToolSpec", "ToolContext", "ToolResult"]
