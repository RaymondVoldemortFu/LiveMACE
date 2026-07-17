"""Public Prompt metadata and rendered content contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
import re

from .common import JsonValue, _freeze_mapping, _require_non_empty


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: str
    required_variables: tuple[str, ...]
    optional_variables: Mapping[str, JsonValue] = field(default_factory=dict)
    content_type: str = "text/plain"

    def __post_init__(self) -> None:
        _require_non_empty(self.id, "id")
        _require_non_empty(self.version, "version")
        _require_non_empty(self.content_type, "content_type")
        if "." not in self.id:
            raise ValueError("prompt id must use a namespace")
        if not isinstance(self.required_variables, tuple):
            raise TypeError("required_variables must be a tuple")
        for name in self.required_variables:
            _require_non_empty(name, "required variable")
        optional = _freeze_mapping(self.optional_variables, "optional_variables")
        if set(self.required_variables).intersection(optional):
            raise ValueError("required and optional variables must not overlap")
        object.__setattr__(self, "optional_variables", optional)


@dataclass(frozen=True)
class RenderedPrompt:
    spec: PromptSpec
    content: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PromptSpec):
            raise TypeError("spec must be PromptSpec")
        if not isinstance(self.content, str):
            raise TypeError("content must be str")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 hex digest")


__all__ = ["PromptSpec", "RenderedPrompt"]

