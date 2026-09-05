"""Adapters from public LLM/Tool ports to the legacy Agent loop surface."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from benchmark.builtin.tools._support import legacy_tool_name
from benchmark.contracts import (
    AgentRuntimeError,
    ComponentConfigError,
    JsonValue,
    ToolResult,
    ToolRuntimeError,
    to_jsonable,
)
from benchmark.providers import LLMRequest, LLMResponse, LLMToolCall
from benchmark.tools import ToolSpecSource
from services.agent.tools import Tool, ToolRegistry

_TRADE_RUNTIME_ARGUMENTS = frozenset({"decision_round_id", "tool_call_id"})


@dataclass(frozen=True)
class _LegacyLLMMessage:
    content: str
    tool_calls: list[dict[str, JsonValue]]
    assistant_message: dict[str, JsonValue]


class PublicLLMBridge:
    """Expose the legacy ``call()`` surface over ``LLMClientPort.complete()``."""

    def __init__(self, llm: Any) -> None:
        if not callable(getattr(llm, "complete", None)):
            raise TypeError("llm must implement complete()")
        self._llm = llm
        model = getattr(llm, "model", None)
        self.model = model if isinstance(model, str) else None

    def call(
        self,
        messages: Sequence[Mapping[str, JsonValue]],
        tools: Sequence[Mapping[str, JsonValue]] | None = None,
        **kwargs: Any,
    ) -> _LegacyLLMMessage:
        metadata: dict[str, JsonValue] = {}
        timeout = kwargs.get("timeout")
        if timeout is not None:
            metadata["timeout_seconds"] = timeout
        response_format = kwargs.get("response_format")
        if response_format is not None:
            metadata["response_format"] = response_format
        response = self._llm.complete(
            LLMRequest(
                messages=tuple(dict(message) for message in messages),
                model=None,
                tools=tuple(dict(tool) for tool in (tools or ())),
                metadata=metadata,
            )
        )
        if not isinstance(response, LLMResponse):
            raise AgentRuntimeError(
                "LLMClientPort.complete() must return LLMResponse",
                code="PUBLIC_LLM_RESULT_INVALID",
            )
        tool_calls = _legacy_tool_calls(response)
        return _LegacyLLMMessage(
            response.content,
            tool_calls,
            _assistant_message(response, tool_calls),
        )

    @staticmethod
    def build_assistant_message_dict(
        response: _LegacyLLMMessage,
    ) -> dict[str, JsonValue]:
        return dict(response.assistant_message)

    def is_gemini_model(self) -> bool:
        capability = getattr(self._llm, "requires_post_tool_user_message", None)
        return bool(capability()) if callable(capability) else False


class InvokerBackedToolRegistry(ToolRegistry):
    """Present a public ToolInvoker as the legacy ToolRegistry surface."""

    def __init__(self, invoker: Any) -> None:
        super().__init__()
        self._invoker = invoker
        specs = ()
        if isinstance(invoker, ToolSpecSource):
            specs = tuple(invoker.list_specs())
        for spec in specs:
            self.register(
                Tool(
                    name=legacy_tool_name(spec.name),
                    description=spec.description,
                    parameters=dict(spec.input_schema),
                    func=_invoker_func(invoker, spec.name),
                )
            )


def legacy_llm_surface(llm: Any, *, code: str, message: str) -> Any:
    legacy = getattr(llm, "legacy_client", None)
    if legacy is not None and callable(getattr(legacy, "call", None)):
        return legacy
    if callable(getattr(llm, "call", None)) and callable(
        getattr(llm, "build_assistant_message_dict", None)
    ):
        return llm
    if callable(getattr(llm, "complete", None)):
        return PublicLLMBridge(llm)
    raise ComponentConfigError(message, code=code)


def legacy_tool_registry(tools: Any) -> ToolRegistry:
    if isinstance(tools, ToolRegistry):
        return tools
    return InvokerBackedToolRegistry(tools)


def _invoker_func(invoker: Any, public_name: str):
    def invoke(**arguments: Any) -> JsonValue:
        payload = dict(arguments)
        if public_name == "core.execute_trade":
            payload = {
                key: value
                for key, value in arguments.items()
                if key not in _TRADE_RUNTIME_ARGUMENTS
            }
        try:
            result = invoker.call(public_name, payload)
        except ToolRuntimeError:
            raise
        if not isinstance(result, ToolResult):
            raise AgentRuntimeError(
                "ToolInvoker.call() must return ToolResult",
                code="PUBLIC_TOOL_RESULT_INVALID",
                details={"tool_name": public_name},
            )
        if result.ok:
            return to_jsonable(result.value)
        return {
            "error": result.error_message or result.error_code or "Tool call failed"
        }

    return invoke


def _legacy_tool_calls(response: LLMResponse) -> list[dict[str, JsonValue]]:
    raw_calls = response.raw.get("tool_calls")
    raw_sequence = raw_calls if isinstance(raw_calls, (list, tuple)) else ()
    return [
        _legacy_tool_call(
            tool_call, raw_sequence[index] if index < len(raw_sequence) else None
        )
        for index, tool_call in enumerate(response.tool_calls)
    ]


def _legacy_tool_call(
    tool_call: LLMToolCall,
    raw: JsonValue,
) -> dict[str, JsonValue]:
    payload = dict(raw) if isinstance(raw, Mapping) else {}
    raw_function = payload.get("function")
    function = dict(raw_function) if isinstance(raw_function, Mapping) else {}
    function["name"] = legacy_tool_name(tool_call.name)
    function["arguments"] = json.dumps(
        to_jsonable(tool_call.arguments),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    payload["id"] = tool_call.id
    payload["type"] = str(payload.get("type") or "function")
    payload["function"] = function
    return to_jsonable(payload)


def _assistant_message(
    response: LLMResponse,
    tool_calls: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    message = dict(response.raw)
    message["role"] = "assistant"
    message["content"] = response.content
    if tool_calls:
        message["tool_calls"] = tool_calls
    else:
        message.pop("tool_calls", None)
    return to_jsonable(message)


__all__ = [
    "InvokerBackedToolRegistry",
    "PublicLLMBridge",
    "legacy_llm_surface",
    "legacy_tool_registry",
]
