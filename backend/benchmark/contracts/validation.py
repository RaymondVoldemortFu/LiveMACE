"""Shared immutable validation reports for public benchmark components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .common import JsonValue, _freeze_mapping


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str
    validator: str = ""
    code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise TypeError("path must be a string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be a non-empty string")
        if not isinstance(self.validator, str) or not isinstance(self.code, str):
            raise TypeError("validator and code must be strings")


def issue_sort_key(issue: ValidationIssue) -> tuple[str, str, str, str]:
    return (issue.path, issue.code, issue.validator, issue.message)


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()
    normalized_config: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.errors, tuple) or not all(
            isinstance(item, ValidationIssue) for item in self.errors
        ):
            raise TypeError("errors must be a tuple of ValidationIssue")
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(item, ValidationIssue) for item in self.warnings
        ):
            raise TypeError("warnings must be a tuple of ValidationIssue")
        errors = tuple(sorted(self.errors, key=issue_sort_key))
        warnings = tuple(sorted(self.warnings, key=issue_sort_key))
        if self.valid != (not errors):
            raise ValueError("valid must match whether errors is empty")
        object.__setattr__(self, "errors", errors)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(
            self,
            "normalized_config",
            _freeze_mapping(self.normalized_config, "normalized_config"),
        )


__all__ = ["ValidationIssue", "ValidationReport", "issue_sort_key"]
