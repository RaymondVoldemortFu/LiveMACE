"""Built-in account and position state Tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from benchmark.contracts import ACCOUNT_READ, JsonValue, SideEffect, ToolContext
from benchmark.builtin.tools._support import BoundCallableTool, spec
from benchmark.builtin.tools.history import HistoryDecisionsTool

ACCOUNT_PARAMETERS: dict[str, JsonValue] = {"type": "object", "properties": {}}

ACCOUNT_SPEC = spec(
    "core.account_state",
    "Get current account balances and open positions.",
    ACCOUNT_PARAMETERS,
    side_effect=SideEffect.READ_ONLY,
    capabilities=(ACCOUNT_READ,),
)


def _serialize_account(account: Any) -> dict[str, Any] | None:
    if not account:
        return None
    return {
        "id": account.id,
        "name": account.name,
        "cash": float(account.current_cash),
        "frozen": float(account.frozen_cash),
    }


def _serialize_position(pos: Any) -> dict[str, Any]:
    return {
        "symbol": pos.symbol,
        "quantity": float(pos.quantity),
        "avg_cost": float(pos.avg_cost),
        "leverage": pos.leverage,
        "side": pos.side,
        "market": str(pos.market),
    }


def _default_account_reader(account_id: int) -> dict[str, Any]:
    from database.connection import SessionLocal
    from repositories.account_repo import get_account
    from repositories.position_repo import list_positions

    db = SessionLocal()
    try:
        return {
            "account": _serialize_account(get_account(db, account_id)),
            "positions": [_serialize_position(item) for item in list_positions(db, account_id)],
        }
    finally:
        db.close()


class AccountStateTool(BoundCallableTool):
    def __init__(self, *, account_reader: Callable[[int], Any] | None = None) -> None:
        self._account_reader = account_reader or _default_account_reader
        super().__init__(ACCOUNT_SPEC, self._invoke)

    def _invoke(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        del arguments
        return self._account_reader(context.account_id)


class AccountToolsProvider:
    def __init__(
        self,
        *,
        account_reader: Callable[[int], Any] | None = None,
        history_reader: Callable[[int, int], Any] | None = None,
    ) -> None:
        self._tools = (
            AccountStateTool(account_reader=account_reader),
            HistoryDecisionsTool(history_reader=history_reader),
        )

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools


__all__ = [
    "ACCOUNT_SPEC",
    "AccountStateTool",
    "AccountToolsProvider",
    "_serialize_account",
    "_serialize_position",
]
