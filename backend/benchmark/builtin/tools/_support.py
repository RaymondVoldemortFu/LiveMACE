"""Shared helpers for built-in Tool providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from inspect import isawaitable
from typing import Any
from uuid import uuid4

from benchmark.contracts import (
    JsonValue,
    SideEffect,
    ToolContext,
    ToolResult,
    ToolRuntimeError,
    ToolSpec,
    to_jsonable,
)

PUBLIC_TO_LEGACY_TOOL_NAMES = {
    "core.execute_trade": "execute_trade",
    "core.market_snapshot": "get_market_snapshot",
    "core.kline_history": "get_kline_history",
    "core.account_state": "get_account_state",
    "core.decision_history": "get_history_decisions",
    "core.memory_add": "memory_add",
    "core.memory_search": "memory_search",
    "core.search": "consult_search_agent",
    "core.execute_shell_command": "execute_shell_command",
    "core.read_file": "read_file",
    "core.write_file": "write_file",
    "core.run_python_script": "run_python_script",
}

LEGACY_TO_PUBLIC_TOOL_NAMES = {
    legacy: public for public, legacy in PUBLIC_TO_LEGACY_TOOL_NAMES.items()
}


def public_tool_name(name: str) -> str:
    if name in LEGACY_TO_PUBLIC_TOOL_NAMES:
        return LEGACY_TO_PUBLIC_TOOL_NAMES[name]
    if name.startswith("public.") or name.startswith("core."):
        return name
    return f"public.{name}"


def legacy_tool_name(name: str) -> str:
    if name in PUBLIC_TO_LEGACY_TOOL_NAMES:
        return PUBLIC_TO_LEGACY_TOOL_NAMES[name]
    if name.startswith("public."):
        return name.removeprefix("public.")
    if name.startswith("core."):
        return name.removeprefix("core.")
    return name


def make_tool_context(
    *,
    account_id: int,
    trace_id: str | None = None,
    decision_round_id: str | None = None,
    call_id: str | None = None,
    capabilities: frozenset[str] | None = None,
) -> ToolContext:
    from benchmark.contracts import KNOWN_CAPABILITIES

    return ToolContext(
        account_id=account_id,
        decision_round_id=decision_round_id or "legacy-round",
        trace_id=trace_id or "legacy-trace",
        call_id=call_id or uuid4().hex,
        capabilities=capabilities if capabilities is not None else frozenset(KNOWN_CAPABILITIES),
    )


class BoundCallableTool:
    """A public Tool backed by a synchronous callable that receives ToolContext."""

    def __init__(
        self,
        spec: ToolSpec,
        handler: Callable[[ToolContext, Mapping[str, JsonValue]], Any],
    ) -> None:
        if not isinstance(spec, ToolSpec):
            raise TypeError("spec must be ToolSpec")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._spec = spec
        self._handler = handler

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(
        self,
        context: ToolContext,
        arguments: Mapping[str, JsonValue],
    ) -> ToolResult:
        if not isinstance(context, ToolContext):
            raise TypeError("context must be ToolContext")
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        result = self._handler(context, arguments)
        if isawaitable(result):
            raise ToolRuntimeError(
                "built-in Tool returned an awaitable",
                code="ASYNC_TOOL_UNSUPPORTED",
                details={"tool_name": self._spec.name},
            )
        if isinstance(result, ToolResult):
            return result
        try:
            return ToolResult(ok=True, value=to_jsonable(result))
        except (TypeError, ValueError) as exc:
            raise ToolRuntimeError(
                "built-in Tool returned a non-JSON result",
                code="TOOL_OUTPUT_INVALID",
                details={"tool_name": self._spec.name},
            ) from exc


def tool_result_error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ToolResult:
    return ToolResult(
        ok=False,
        error_code=code,
        error_message=message,
        retryable=retryable,
        metadata=dict(metadata or {}),
    )


def invoke_as_legacy(
    tool: BoundCallableTool,
    *,
    account_id: int,
    trace_id: str | None,
    arguments: Mapping[str, Any],
    decision_round_id: str | None = None,
) -> Any:
    result = tool.invoke(
        make_tool_context(
            account_id=account_id,
            trace_id=trace_id,
            decision_round_id=decision_round_id,
        ),
        dict(arguments),
    )
    if result.ok:
        return result.value
    return {"error": result.error_message or result.error_code or "Tool call failed"}


def spec(
    name: str,
    description: str,
    parameters: Mapping[str, JsonValue],
    *,
    side_effect: SideEffect,
    capabilities: tuple[str, ...],
    timeout_seconds: float = 30.0,
    cacheable: bool = False,
    output_schema: Mapping[str, JsonValue] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        input_schema=dict(parameters),
        output_schema=dict(output_schema or {"type": "object"}),
        side_effect=side_effect,
        timeout_seconds=timeout_seconds,
        cacheable=cacheable,
        required_capabilities=capabilities,
    )


class LazyContainerSandbox:
    """Defer Docker client construction until a sandbox Tool is invoked."""

    def __init__(self) -> None:
        self._service: Any = None

    def _service_instance(self) -> Any:
        if self._service is None:
            from services.container_service import ContainerService

            self._service = ContainerService()
        return self._service

    def execute_command(self, account_id: int, command: str) -> Any:
        return self._service_instance().execute_command(account_id, command)

    def read_file(self, account_id: int, file_path: str) -> Any:
        return self._service_instance().read_file(account_id, file_path)

    def write_file(self, account_id: int, file_path: str, content: str) -> Any:
        return self._service_instance().write_file(account_id, file_path, content)


__all__ = [
    "BoundCallableTool",
    "LEGACY_TO_PUBLIC_TOOL_NAMES",
    "LazyContainerSandbox",
    "PUBLIC_TO_LEGACY_TOOL_NAMES",
    "invoke_as_legacy",
    "legacy_tool_name",
    "make_tool_context",
    "public_tool_name",
    "spec",
    "tool_result_error",
]
