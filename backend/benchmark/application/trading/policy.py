"""Trading policy helpers."""

from __future__ import annotations

from decimal import Decimal

from benchmark.contracts import Market, TradeCommand


def normalize_trade_command(command: TradeCommand) -> TradeCommand:
    symbol = str(command.symbol or "").strip().upper()
    direction = command.direction.lower() if isinstance(command.direction, str) else command.direction
    sizing_mode = command.sizing_mode.lower() if isinstance(command.sizing_mode, str) else command.sizing_mode
    operation = command.operation.lower()
    leverage = max(1, min(int(command.leverage or 1), 10))
    sizing_value = command.sizing_value
    if sizing_value is not None and not isinstance(sizing_value, Decimal):
        sizing_value = Decimal(str(sizing_value))
    return TradeCommand(
        account_id=command.account_id,
        operation=operation,
        market=command.market if isinstance(command.market, Market) else Market(str(command.market).upper()),
        symbol=symbol,
        direction=direction,
        sizing_mode=sizing_mode,
        sizing_value=sizing_value,
        leverage=leverage,
        reason=command.reason,
        idempotency_key=command.idempotency_key,
    )


__all__ = ["normalize_trade_command"]
