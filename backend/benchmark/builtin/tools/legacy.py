"""Adapters that present built-in providers through the legacy ToolRegistry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from benchmark.builtin.tools._support import BoundCallableTool, invoke_as_legacy, legacy_tool_name
from services.agent.tools import Tool


def as_legacy_tool(
    tool: BoundCallableTool,
    *,
    account_id: int,
    trace_id: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> Tool:
    spec = tool.spec

    def func(**arguments: Any) -> Any:
        return invoke_as_legacy(
            tool,
            account_id=account_id,
            trace_id=trace_id,
            arguments=arguments,
        )

    payload = dict(metadata or {})
    return Tool(
        name=legacy_tool_name(spec.name),
        description=spec.description,
        parameters=dict(spec.input_schema),
        func=func,
        metadata=payload,
    )


def register_legacy_tools(
    registry: Any,
    tools: Iterable[BoundCallableTool],
    *,
    account_id: int,
    trace_id: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    count = 0
    for tool in tools:
        legacy = as_legacy_tool(
            tool, account_id=account_id, trace_id=trace_id, metadata=metadata
        )
        registry.register(legacy)
        count += 1
    return count


__all__ = ["as_legacy_tool", "register_legacy_tools"]
