"""Built-in memory add/search Tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from benchmark.contracts import MEMORY_READ, MEMORY_WRITE, JsonValue, SideEffect, ToolContext
from benchmark.builtin.tools._support import BoundCallableTool, spec

MEMORY_ADD_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "experience": {
            "type": "string",
            "description": "A reusable trading rule in the format: [CONDITION] → [OBSERVATION] → [RULE]. Strip specific dates and prices. Max 1-3 sentences.",
        },
        "account_id": {"type": "string", "description": "Your account ID"},
        "market": {
            "type": "string",
            "enum": ["CRYPTO", "US"],
            "description": "The market this memory belongs to. Use CRYPTO for crypto trading rules, US for US stock trading rules.",
        },
        "metadata": {
            "type": "string",
            "description": "Optional JSON string with additional context",
            "default": None,
        },
    },
    "required": ["experience", "account_id", "market"],
}

MEMORY_SEARCH_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "A pattern description of the market condition you want rules for (e.g. 'high leverage risk in downtrend', 'SOL support breakdown patterns')",
        },
        "account_id": {"type": "string", "description": "Your account ID"},
        "market": {
            "type": "string",
            "enum": ["CRYPTO", "US"],
            "description": "The market to search memories for. Use CRYPTO for crypto rules, US for US stock rules.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of memories to return (default: 2, max: 5)",
            "default": 2,
        },
    },
    "required": ["query", "account_id", "market"],
}

MEMORY_ADD_SPEC = spec(
    "core.memory_add",
    "Store a reusable trading rule to long-term memory. Format: [CONDITION] → [OBSERVATION] → [RULE], 1-3 sentences max. Do NOT store news, specific dates/prices, or event logs — only generalizable patterns. You MUST specify the market parameter.",
    MEMORY_ADD_PARAMETERS,
    side_effect=SideEffect.MEMORY_WRITE,
    capabilities=(MEMORY_WRITE,),
)

MEMORY_SEARCH_SPEC = spec(
    "core.memory_search",
    "Search long-term memory for trading rules relevant to current market conditions. Query with pattern descriptions (e.g. 'altcoin oversold during BTC downtrend'), not specific events or dates. You MUST specify the market parameter to search only relevant memories.",
    MEMORY_SEARCH_PARAMETERS,
    side_effect=SideEffect.READ_ONLY,
    capabilities=(MEMORY_READ,),
)

MemoryAddFn = Callable[[ToolContext, Mapping[str, JsonValue]], Any]
MemorySearchFn = Callable[[ToolContext, Mapping[str, JsonValue]], Any]


def _legacy_memory_pair(db: Any, *, trace_id: str | None, bound_account_id: str | None):
    from services.agent.memory_tools import create_memory_tools

    return create_memory_tools(db, trace_id=trace_id, bound_account_id=bound_account_id)


def _call_legacy_add(add_tool: Any, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
    metadata = arguments.get("metadata")
    return add_tool.func(
        experience=str(arguments["experience"]),
        account_id=str(context.account_id),
        market=str(arguments["market"]),
        metadata=None if metadata is None else str(metadata),
    )


def _call_legacy_search(
    search_tool: Any, context: ToolContext, arguments: Mapping[str, JsonValue]
) -> Any:
    raw_limit = arguments.get("limit", 2)
    if raw_limit is None:
        raw_limit = 2
    return search_tool.func(
        query=str(arguments["query"]),
        account_id=str(context.account_id),
        market=str(arguments["market"]),
        limit=int(raw_limit),
    )


class MemoryToolsProvider:
    def __init__(
        self,
        *,
        db: Any | None = None,
        trace_id: str | None = None,
        bound_account_id: str | None = None,
        add_fn: MemoryAddFn | None = None,
        search_fn: MemorySearchFn | None = None,
    ) -> None:
        self._db = db
        self._trace_id = trace_id
        self._bound_account_id = bound_account_id
        self._add_fn = add_fn
        self._search_fn = search_fn
        if db is not None and add_fn is None and search_fn is None:
            add_tool, search_tool = _legacy_memory_pair(
                db, trace_id=trace_id, bound_account_id=bound_account_id
            )
            self._add_fn = lambda context, arguments: _call_legacy_add(
                add_tool, context, arguments
            )
            self._search_fn = lambda context, arguments: _call_legacy_search(
                search_tool, context, arguments
            )
        self._tools = (
            BoundCallableTool(MEMORY_ADD_SPEC, self._add),
            BoundCallableTool(MEMORY_SEARCH_SPEC, self._search),
        )

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools

    def _add(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        if self._add_fn is not None:
            return self._add_fn(context, arguments)
        from database.connection import SessionLocal

        db = SessionLocal()
        try:
            add_tool, _search_tool = _legacy_memory_pair(
                db,
                trace_id=context.trace_id,
                bound_account_id=str(context.account_id),
            )
            return _call_legacy_add(add_tool, context, arguments)
        finally:
            db.close()

    def _search(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        if self._search_fn is not None:
            return self._search_fn(context, arguments)
        from database.connection import SessionLocal

        db = SessionLocal()
        try:
            _add_tool, search_tool = _legacy_memory_pair(
                db,
                trace_id=context.trace_id,
                bound_account_id=str(context.account_id),
            )
            return _call_legacy_search(search_tool, context, arguments)
        finally:
            db.close()


__all__ = ["MEMORY_ADD_SPEC", "MEMORY_SEARCH_SPEC", "MemoryToolsProvider"]
