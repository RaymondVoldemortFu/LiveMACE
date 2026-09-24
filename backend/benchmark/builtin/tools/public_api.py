"""Built-in public-API Tools with namespaced ids and source metadata."""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Any

from benchmark.contracts import NETWORK_READ, JsonValue, SideEffect, ToolContext
from benchmark.builtin.tools._support import BoundCallableTool, spec
from benchmark.contracts.identifiers import require_identifier

_PUBLIC_NAMESPACE = "public"


def _load_schema_entries() -> list[dict[str, Any]]:
    from services.agent.public_apis_registry import _load_tools_schema, _normalize_json_schema

    is_unavailable = None
    if os.getenv("WAVE3_PRODUCTION", "false").lower() == "true":
        from services.agent.public_apis_registry import _load_api_server_module

        is_unavailable = _load_api_server_module()._wave3_api_unavailable
    entries: list[dict[str, Any]] = []
    for entry in _load_tools_schema():
        if not isinstance(entry, dict) or entry.get("type") != "function":
            continue
        function_block = entry.get("function") or {}
        name = function_block.get("name")
        if not name or (is_unavailable is not None and is_unavailable(name)):
            continue
        parameters = function_block.get("parameters") or {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
        if isinstance(parameters, dict):
            parameters = _normalize_json_schema(parameters)
        else:
            parameters = {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        entries.append(
            {
                "legacy_name": str(name),
                "description": function_block.get("description") or f"Call the {name} public API.",
                "parameters": parameters,
            }
        )
    return entries


def _public_name(legacy_name: str) -> str | None:
    candidate = f"{_PUBLIC_NAMESPACE}.{legacy_name.strip().lower()}"
    try:
        require_identifier(candidate, "tool name")
    except ValueError:
        return None
    return candidate


def _make_handler(legacy_name: str):
    def handler(context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        del context
        from services.agent.public_apis_registry import _load_api_server_module

        return _load_api_server_module()._run_api(legacy_name, dict(arguments))

    return handler


class PublicApiToolsProvider:
    """Expose generated public APIs as `public.*` Tools.

    Each Tool keeps the original description and parameter schema. The
    namespaced id plus `source=public-apis` on the Tool object identify origin.
    """

    def __init__(self, *, limit: int | None = None) -> None:
        tools: list[BoundCallableTool] = []
        seen: set[str] = set()
        for entry in _load_schema_entries():
            public_name = _public_name(entry["legacy_name"])
            if public_name is None:
                raise ValueError(
                    "public API tool name is not a valid identifier: "
                    f"{entry['legacy_name']!r}"
                )
            if public_name in seen:
                raise ValueError(f"duplicate public API tool name: {public_name}")
            seen.add(public_name)
            tool = BoundCallableTool(
                spec(
                    public_name,
                    str(entry["description"]),
                    entry["parameters"],
                    side_effect=SideEffect.EXTERNAL_READ,
                    capabilities=(NETWORK_READ,),
                ),
                _make_handler(entry["legacy_name"]),
            )
            object.__setattr__(tool, "source", "public-apis")
            object.__setattr__(tool, "legacy_name", entry["legacy_name"])
            tools.append(tool)
            if limit is not None and len(tools) >= limit:
                break
        if not tools:
            raise ValueError("public API schema produced no tools")
        self._tools = tuple(tools)

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools


__all__ = ["PublicApiToolsProvider"]
