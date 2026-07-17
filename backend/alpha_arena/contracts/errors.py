"""Stable framework-level exceptions exposed to extension authors."""

from __future__ import annotations

from typing import Mapping

from .common import JsonValue, _freeze_mapping, to_jsonable


class AlphaArenaError(Exception):
    default_code = "ALPHA_ARENA_ERROR"

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


class ExtensionManifestError(AlphaArenaError):
    default_code = "EXTENSION_MANIFEST_INVALID"


class ExtensionLoadError(AlphaArenaError):
    default_code = "EXTENSION_LOAD_FAILED"


class ComponentNotFoundError(AlphaArenaError):
    default_code = "COMPONENT_NOT_FOUND"


class ComponentConflictError(AlphaArenaError):
    default_code = "COMPONENT_CONFLICT"


class ComponentConfigError(AlphaArenaError):
    default_code = "COMPONENT_CONFIG_INVALID"


class AgentRuntimeError(AlphaArenaError):
    default_code = "AGENT_RUNTIME_ERROR"


class ToolRuntimeError(AlphaArenaError):
    default_code = "TOOL_RUNTIME_ERROR"


class PromptRenderError(AlphaArenaError):
    default_code = "PROMPT_RENDER_ERROR"


class ProviderError(AlphaArenaError):
    default_code = "PROVIDER_ERROR"


class TradeGatewayError(AlphaArenaError):
    default_code = "TRADE_GATEWAY_ERROR"


__all__ = [
    "AlphaArenaError",
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
