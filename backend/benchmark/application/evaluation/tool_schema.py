"""Resolve a trace's tool schema from the catalog version that produced it."""

from __future__ import annotations

from typing import Any

from benchmark.builtin.tools._support import LEGACY_TO_PUBLIC_TOOL_NAMES, PUBLIC_TO_LEGACY_TOOL_NAMES, public_tool_name, legacy_tool_name
from benchmark.contracts import BenchmarkError, to_jsonable


def tool_name_candidates(name: str) -> tuple[str, ...]:
    """Trace rows use legacy names; runtime events use public catalog names."""

    names = [name, public_tool_name(name), legacy_tool_name(name)]
    mapped = LEGACY_TO_PUBLIC_TOOL_NAMES.get(name)
    if mapped:
        names.append(mapped)
    legacy = PUBLIC_TO_LEGACY_TOOL_NAMES.get(name)
    if legacy:
        names.append(legacy)
    return tuple(dict.fromkeys(names))


def _lookup_version(name: str, versions: dict[str, str | None]) -> str | None:
    for candidate in tool_name_candidates(name):
        version = versions.get(candidate)
        if isinstance(version, str) and version and version != "unknown":
            return version
    return None


def _installed_name(runtime, name: str) -> str | None:
    for candidate in tool_name_candidates(name):
        try:
            runtime.tools.get(candidate)
        except BenchmarkError:
            continue
        return candidate
    return None


def resolve_recorded_tool(
    runtime,
    requested_name: str,
    recorded_versions: dict[str, str | None],
    component_versions: dict[str, str | None],
) -> dict[str, Any]:
    """Resolve one trace tool call without substituting today's catalog version."""

    installed = _installed_name(runtime, requested_name)
    version = _lookup_version(requested_name, recorded_versions)
    if version is None:
        version = _lookup_version(requested_name, component_versions)
    catalog_name = installed or LEGACY_TO_PUBLIC_TOOL_NAMES.get(requested_name) or requested_name
    resolved = resolve_tool_schema(runtime, catalog_name, version)
    resolved["requested_name"] = requested_name
    if installed is not None:
        resolved["name"] = installed
    return resolved


def resolve_tool_schema(runtime, tool_name: str, version: str | None) -> dict[str, Any]:
    """Return the installed schema only when the recorded version matches.

    A missing component or a different installed version is ``unavailable``.
    The current catalog version is never substituted for a historical one.
    """

    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("tool_name must be a non-empty string")
    if version is None or version == "" or version == "unknown":
        return {
            "name": tool_name,
            "version": None if version in (None, "") else version,
            "status": "unavailable",
            "schema": None,
            "reason": "version_missing",
        }
    try:
        entry = runtime.tools.get(tool_name)
    except BenchmarkError as exc:
        return {
            "name": tool_name,
            "version": version,
            "status": "unavailable",
            "schema": None,
            "reason": exc.code,
        }
    installed = entry.extension.version
    if installed != version:
        return {
            "name": tool_name,
            "version": version,
            "status": "unavailable",
            "schema": None,
            "reason": "version_not_installed",
        }
    return {
        "name": tool_name,
        "version": installed,
        "status": "available",
        "schema": to_jsonable(entry.spec.input_schema),
        "reason": None,
    }
