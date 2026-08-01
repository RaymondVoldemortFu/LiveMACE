"""Explicit validation and canonicalization for trade commands."""

from __future__ import annotations

from decimal import Decimal

from benchmark.contracts import Market, TradeCommand
from benchmark.contracts.errors import TradeGatewayError
from benchmark.infrastructure.market.symbols import resolve_symbol_market


def normalize_trade_command(command: TradeCommand) -> TradeCommand:
    if not isinstance(command, TradeCommand):
        raise TypeError("command must be TradeCommand")
    symbol = command.symbol.strip().upper()
    operation = command.operation.strip().lower()
    direction = _lower_optional(command.direction, "direction")
    sizing_mode = _lower_optional(command.sizing_mode, "sizing_mode")

    if command.leverage > 10:
        raise _policy_error("leverage must not exceed 10", "LEVERAGE_INVALID")
    leverage = command.leverage
    if command.market is Market.US and leverage != 1:
        raise _policy_error("US market does not support leverage", "LEVERAGE_INVALID")
    if direction is not None and direction not in {"long", "short"}:
        raise _policy_error("direction must be long or short", "DIRECTION_INVALID")
    if symbol:
        try:
            symbol = resolve_symbol_market(symbol, command.market).symbol
        except ValueError as exc:
            raise _policy_error(str(exc), "SYMBOL_INVALID") from exc

    if operation in {"open", "close"}:
        if direction is None:
            raise _policy_error("direction is required", "DIRECTION_INVALID")
        allowed_modes = {"portion", "usd"}
        if operation == "close":
            allowed_modes.add("close_ratio")
        if sizing_mode not in allowed_modes:
            raise _policy_error(
                f"unsupported sizing mode for {operation}",
                "SIZING_MODE_INVALID",
            )
        if command.sizing_value is None or not command.sizing_value.is_finite():
            raise _policy_error(
                "sizing_value must be finite",
                "SIZING_VALUE_INVALID",
            )
        if command.sizing_value <= 0:
            raise _policy_error(
                "sizing_value must be positive",
                "SIZING_VALUE_INVALID",
            )
        if sizing_mode in {"portion", "close_ratio"} and command.sizing_value > Decimal("1"):
            raise _policy_error(
                f"{sizing_mode} must not exceed 1",
                "SIZING_VALUE_INVALID",
            )
    elif operation == "all_in":
        if direction is None:
            raise _policy_error("direction is required", "DIRECTION_INVALID")
        if command.sizing_value is not None:
            raise _policy_error(
                "all_in does not accept sizing_value",
                "SIZING_VALUE_INVALID",
            )
        sizing_mode = None
    elif operation in {"hold", "close_all"}:
        if command.sizing_value is not None or sizing_mode is not None:
            raise _policy_error(
                f"{operation} does not accept sizing options",
                "SIZING_VALUE_INVALID",
            )
        if operation == "hold":
            leverage = 1
    else:
        raise _policy_error("unsupported operation", "OPERATION_INVALID")

    return TradeCommand(
        account_id=command.account_id,
        operation=operation,
        market=command.market,
        symbol=symbol,
        direction=direction,
        sizing_mode=sizing_mode,
        sizing_value=command.sizing_value,
        leverage=leverage,
        reason=command.reason,
        idempotency_key=command.idempotency_key,
    )


def _lower_optional(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _policy_error(f"{name} must be non-empty text", f"{name.upper()}_INVALID")
    return value.strip().lower()


def _policy_error(message: str, code: str) -> TradeGatewayError:
    return TradeGatewayError(message, code=code)


__all__ = ["normalize_trade_command"]
