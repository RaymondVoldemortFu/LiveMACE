"""Stable benchmark DTOs and errors for extension authors."""

from .common import (
    AccountView,
    DecisionContext,
    ExtensionRef,
    JsonValue,
    Market,
    PortfolioView,
    PositionView,
    to_jsonable,
)
from .agent import AgentRunResult, ExecutedTradeRef, TerminationReason
from .tool import SideEffect, ToolContext, ToolResult, ToolSpec
from .prompt import PromptSpec, RenderedPrompt
from .trade import TradeCommand, TradeCommandResult
from .errors import (
    AgentRuntimeError,
    AlphaArenaError,
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
    ExtensionLoadError,
    ExtensionManifestError,
    PromptRenderError,
    ProviderError,
    ToolRuntimeError,
    TradeGatewayError,
)

__all__ = [
    "JsonValue",
    "Market",
    "ExtensionRef",
    "AccountView",
    "PositionView",
    "PortfolioView",
    "DecisionContext",
    "to_jsonable",
    "TerminationReason",
    "ExecutedTradeRef",
    "AgentRunResult",
    "SideEffect",
    "ToolSpec",
    "ToolContext",
    "ToolResult",
    "PromptSpec",
    "RenderedPrompt",
    "TradeCommand",
    "TradeCommandResult",
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
