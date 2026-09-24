"""Stable benchmark framework exceptions exposed to extension authors."""

from __future__ import annotations

from typing import Mapping

from .common import JsonValue, _freeze_mapping, to_jsonable

_PROVIDER_RESERVED_DETAIL_KEYS = frozenset({"retryable", "provider_id"})


class BenchmarkError(Exception):
    default_code = "BENCHMARK_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = _freeze_mapping(details or {}, "details")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "message": self.message,
            "details": to_jsonable(self.details),
        }


class ExtensionManifestError(BenchmarkError):
    default_code = "EXTENSION_MANIFEST_INVALID"


class ExtensionLoadError(BenchmarkError):
    default_code = "EXTENSION_LOAD_FAILED"


class ComponentNotFoundError(BenchmarkError):
    default_code = "COMPONENT_NOT_FOUND"


class ComponentConflictError(BenchmarkError):
    default_code = "COMPONENT_CONFLICT"


class ComponentConfigError(BenchmarkError):
    default_code = "COMPONENT_CONFIG_INVALID"


class AgentRuntimeError(BenchmarkError):
    default_code = "AGENT_RUNTIME_ERROR"


class ToolRuntimeError(BenchmarkError):
    default_code = "TOOL_RUNTIME_ERROR"


class PromptRenderError(BenchmarkError):
    default_code = "PROMPT_RENDER_ERROR"


class ProviderError(BenchmarkError):
    """Provider failures uniformly expose code/message/retryable/provider_id."""

    default_code = "PROVIDER_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
        retryable: bool = False,
        provider_id: str = "unknown",
    ) -> None:
        if not isinstance(retryable, bool):
            raise TypeError("retryable must be bool")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        safe_details = dict(details or {})
        reserved = _PROVIDER_RESERVED_DETAIL_KEYS.intersection(safe_details)
        if reserved:
            raise ValueError(
                "details must not contain reserved ProviderError fields: "
                + ", ".join(sorted(reserved))
            )
        super().__init__(message, code=code, details=safe_details)
        self.retryable = retryable
        self.provider_id = provider_id

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "provider_id": self.provider_id,
            "details": to_jsonable(self.details),
        }


class TradeGatewayError(BenchmarkError):
    default_code = "TRADE_GATEWAY_ERROR"


__all__ = [
    "BenchmarkError",
    "ExtensionManifestError",
    "ExtensionLoadError",
    "ComponentNotFoundError",
    "ComponentConflictError",
    "ComponentConfigError",
    "AgentRuntimeError",
    "ToolRuntimeError",
    "PromptRenderError",
    "ProviderError",
    "TradeGatewayError",
]
