"""Built-in account state history Tool."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from benchmark.contracts import ACCOUNT_READ, JsonValue, SideEffect, ToolContext
from benchmark.builtin.tools._support import BoundCallableTool, spec

HISTORY_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "description": "Number of recent decisions to retrieve (default 5, max 20)",
            "default": 5,
            "minimum": 1,
            "maximum": 20,
        }
    },
    "required": [],
}

HISTORY_DESCRIPTION = """Get your recent trading decision history with P&L tracking. Call this EARLY in your workflow (after get_account_state) to understand what you did recently and avoid repeating mistakes.

Returns:
- Each decision: operation, symbol, direction, leverage, reason, whether it was executed
- Account performance since each decision: total assets then vs now, change amount and percent
- Position P&L for open positions from past decisions: gross P&L, fees, interest, net unrealized P&L, return on margin
- Execution details: actual fill price and quantity

When to call:
- At the start of each decision cycle to review recent actions
- Before opening a position on a symbol you recently traded
- When you want to evaluate whether your recent strategy is working

Do NOT call this repeatedly in the same decision cycle. One call with limit=5 is usually sufficient."""

HISTORY_SPEC = spec(
    "core.decision_history",
    HISTORY_DESCRIPTION,
    HISTORY_PARAMETERS,
    side_effect=SideEffect.READ_ONLY,
    capabilities=(ACCOUNT_READ,),
)


def _default_history_reader(account_id: int, limit: int) -> Any:
    from database.connection import SessionLocal
    from services.agent.history_tool import get_decision_history

    db = SessionLocal()
    try:
        return get_decision_history(account_id, min(limit, 20), db)
    finally:
        db.close()


class HistoryDecisionsTool(BoundCallableTool):
    def __init__(
        self,
        *,
        history_reader: Callable[[int, int], Any] | None = None,
    ) -> None:
        self._history_reader = history_reader or _default_history_reader
        super().__init__(HISTORY_SPEC, self._invoke)

    def _invoke(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        raw_limit = arguments.get("limit", 5)
        if raw_limit is None:
            raw_limit = 5
        limit = int(raw_limit)
        return self._history_reader(context.account_id, min(limit, 20))


class HistoryToolsProvider:
    def __init__(self, *, history_reader: Callable[[int, int], Any] | None = None) -> None:
        self._tools = (HistoryDecisionsTool(history_reader=history_reader),)

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools


__all__ = ["HISTORY_SPEC", "HistoryDecisionsTool", "HistoryToolsProvider"]
