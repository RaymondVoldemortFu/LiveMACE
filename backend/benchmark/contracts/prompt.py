"""Public benchmark Prompt metadata and rendered content contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
import re

from .common import JsonValue, _freeze_mapping, _require_non_empty
from .identifiers import require_identifier, require_semver, require_variable_name


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: str
    required_variables: tuple[str, ...]
    optional_variables: Mapping[str, JsonValue] = field(default_factory=dict)
    content_type: str = "text/plain"

    def __post_init__(self) -> None:
        require_identifier(self.id, "prompt id")
        require_semver(self.version, "prompt version")
        _require_non_empty(self.content_type, "content_type")
        if not isinstance(self.required_variables, tuple):
            raise TypeError("required_variables must be a tuple")
        for name in self.required_variables:
            require_variable_name(name, "required variable")
        if len(set(self.required_variables)) != len(self.required_variables):
            raise ValueError("required_variables must not contain duplicates")
        optional = _freeze_mapping(self.optional_variables, "optional_variables")
        for name in optional:
            require_variable_name(name, "optional variable")
        if set(self.required_variables).intersection(optional):
            raise ValueError("required and optional variables must not overlap")
        object.__setattr__(self, "optional_variables", optional)


@dataclass(frozen=True)
class PromptSelection:
    prompt_id: str
    version: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.prompt_id, "prompt id")
        if self.version is not None:
            require_semver(self.version, "prompt version")


@dataclass(frozen=True)
class PromptProfileDescriptor:
    id: str
    version: str
    slots: Mapping[str, PromptSelection]

    def __post_init__(self) -> None:
        require_identifier(self.id, "prompt profile id")
        require_semver(self.version, "prompt profile version")
        if not isinstance(self.slots, Mapping) or not self.slots:
            raise ValueError("slots must be a non-empty mapping")
        normalized: dict[str, PromptSelection] = {}
        for slot, selection in self.slots.items():
            require_variable_name(slot, "profile slot")
            if not isinstance(selection, PromptSelection):
                raise TypeError("profile slot values must be PromptSelection")
            normalized[slot] = selection
        object.__setattr__(self, "slots", MappingProxyType(normalized))


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


__all__ = [
    "PromptSpec",
    "PromptSelection",
    "PromptProfileDescriptor",
    "RenderedPrompt",
]
