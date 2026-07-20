"""Shared identifier, variable-name, and SemVer validation helpers."""

from __future__ import annotations

import re

_IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
_VARIABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def require_identifier(value: str, field_name: str = "id") -> None:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not _IDENTIFIER_RE.fullmatch(value)
    ):
        raise ValueError(
            f"{field_name} must be a lowercase ASCII namespaced identifier"
        )


def require_variable_name(value: str, field_name: str = "variable") -> None:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not _VARIABLE_NAME_RE.fullmatch(value)
    ):
        raise ValueError(f"{field_name} must be a lowercase snake_case name")


def require_semver(value: str, field_name: str = "version") -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be SemVer")
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must be SemVer")
    prerelease = match.group(4)
    if prerelease and any(
        item.isdigit() and len(item) > 1 and item.startswith("0")
        for item in prerelease.split(".")
    ):
        raise ValueError(f"{field_name} must be SemVer")


def semver_key(
    version: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...], str]:
    """Return a deterministic SemVer precedence key."""

    require_semver(version)
    match = _SEMVER_RE.fullmatch(version)
    assert match is not None
    major, minor, patch, prerelease, _build = match.groups()
    prerelease_key = tuple(
        (0, int(item)) if item.isdigit() else (1, item)
        for item in (prerelease or "").split(".")
        if item
    )
    return (
        int(major),
        int(minor),
        int(patch),
        int(prerelease is None),
        prerelease_key,
        version,
    )


__all__ = [
    "require_identifier",
    "require_variable_name",
    "require_semver",
    "semver_key",
]
