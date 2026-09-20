"""Private SQLAlchemy bridge for the transitional legacy trade engine."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping


def execute_legacy_trade(session: Any, command: Any) -> Mapping[str, Any]:
    """Execute the fixed legacy implementation in the caller-owned UoW.

    This module is infrastructure-only.  No application protocol accepts a
    callback capable of replacing this function or receiving ``session``.
    """
    from services.agent.trade_execution_tool import _execute_trade_tool_legacy

    return _execute_trade_tool_legacy(
        db=session,
        account_id=command.account_id,
        operation=command.operation,
        symbol=command.symbol,
        market=command.market.value,
        direction=command.direction or "long",
        size_mode=(
            "portion"
            if command.operation == "all_in" or command.sizing_mode == "close_ratio"
            else command.sizing_mode or "portion"
        ),
        target_portion_of_balance=(
            _as_float(command.sizing_value)
            if command.sizing_mode == "portion"
            else None
        ),
        usd_amount=(
            _as_float(command.sizing_value)
            if command.sizing_mode == "usd"
            else None
        ),
        close_ratio=(
            _as_float(command.sizing_value)
            if command.sizing_mode == "close_ratio"
            else None
        ),
        leverage=command.leverage,
        reason=command.reason,
        manage_transaction=False,
        raise_on_error=True,
    )


def _as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
