"""Shared DecisionContext <-> legacy Agent dict conversion for built-in adapters.

The portfolio/prices shape matches ``ai_decision_service._get_portfolio_data``,
which is what ReAct currently receives in production. Incomplete ``execute_trade``
payloads are not rewritten into guessed symbols; they are reported separately.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from benchmark.contracts import (
    AgentRuntimeError,
    DecisionContext,
    ExecutedTradeRef,
    JsonValue,
    Market,
    TerminationReason,
    to_jsonable,
)


def portfolio_from_context(context: DecisionContext) -> dict[str, Any]:
    """Convert a DecisionContext portfolio into the production Agent dict."""

    positions: dict[str, dict[str, Any]] = {}
    for position in context.portfolio.positions:
        quantity = _number(position.quantity)
        if quantity <= 0:
            continue
        avg_cost = _number(position.avg_cost)
        positions[position.symbol] = {
            "quantity": quantity,
            "avg_cost": avg_cost,
            "current_value": quantity * avg_cost,
            "side": (position.side or "LONG").upper(),
            "leverage": position.leverage,
            "market": position.market.value,
        }
    return {
        "account_id": context.portfolio.account.id,
        "cash": _number(context.portfolio.account.current_cash),
        "frozen_cash": _number(context.portfolio.account.frozen_cash),
        "positions": positions,
        "total_assets": _number(context.portfolio.total_assets),
    }


def prices_from_context(context: DecisionContext) -> dict[str, float]:
    return {
        symbol: _number(price) for symbol, price in context.portfolio.prices.items()
    }


def termination_from_legacy_decision(decision: Mapping[str, Any]) -> TerminationReason:
    raw = decision.get("termination_reason")
    if not isinstance(raw, str) or not raw.strip():
        raise AgentRuntimeError(
            "legacy Agent decision is missing termination_reason",
            code="LEGACY_TERMINATION_REASON_MISSING",
        )
    try:
        return TerminationReason(raw)
    except ValueError as exc:
        raise AgentRuntimeError(
            "legacy Agent decision has an unknown termination_reason",
            code="LEGACY_TERMINATION_REASON_INVALID",
            details={"termination_reason": raw},
        ) from exc


def executed_trades_from_legacy(
    items: Any,
) -> tuple[tuple[ExecutedTradeRef, ...], tuple[JsonValue, ...], tuple[JsonValue, ...]]:
    """Split legacy execute_trade payloads into valid refs and leftovers.

    Returns ``(refs, incomplete_payloads, trade_errors)``.
    """

    if items in (None, ()):
        return (), (), ()
    if not isinstance(items, (list, tuple)):
        raise AgentRuntimeError(
            "legacy executed_trades must be a list",
            code="LEGACY_EXECUTED_TRADES_INVALID",
        )

    refs: list[ExecutedTradeRef] = []
    incomplete: list[JsonValue] = []
    errors: list[JsonValue] = []
    for item in items:
        if not isinstance(item, dict):
            incomplete.append({"raw_result": str(item)})
            continue
        payload = dict(item)
        operation = str(payload.get("operation") or "").strip()
        if operation == "hold":
            continue
        if operation == "close_all" and not (payload.get("closed_orders") or []):
            continue
        error_text = payload.get("error")
        if error_text is not None:
            errors.append(
                {
                    "operation": str(payload.get("operation") or ""),
                    "symbol": str(payload.get("symbol") or ""),
                    "error": str(error_text),
                }
            )
        try:
            refs.extend(_trade_refs_from_item(payload))
        except _IncompleteTradeRef:
            incomplete.append(to_jsonable(payload))
    return tuple(refs), tuple(incomplete), tuple(errors)


def nested_executed_trades_from_legacy(
    items: Any,
) -> tuple[tuple[ExecutedTradeRef, ...], tuple[JsonValue, ...], tuple[JsonValue, ...]]:
    """Convert Rule Agent ``{args, result}`` trade records without losing order."""

    if items in (None, ()):
        return (), (), ()
    if not isinstance(items, (list, tuple)):
        raise AgentRuntimeError(
            "legacy executed_trades must be a list",
            code="LEGACY_EXECUTED_TRADES_INVALID",
        )

    refs: list[ExecutedTradeRef] = []
    incomplete: list[JsonValue] = []
    errors: list[JsonValue] = []
    for item in items:
        if not isinstance(item, Mapping):
            incomplete.append({"raw_result": str(item)})
            continue
        args = item.get("args")
        result = item.get("result")
        if not isinstance(args, Mapping) or not isinstance(result, Mapping):
            incomplete.append(to_jsonable(dict(item)))
            continue

        payload = dict(args)
        payload.update(dict(result))
        operation = str(payload.get("operation") or "").strip()
        if operation == "hold":
            continue
        if operation == "close_all" and not (payload.get("closed_orders") or []):
            continue
        error_text = payload.get("error")
        if error_text is not None:
            errors.append(
                {
                    "operation": str(payload.get("operation") or ""),
                    "symbol": str(payload.get("symbol") or ""),
                    "error": str(error_text),
                }
            )
        try:
            refs.extend(_trade_refs_from_item(payload))
        except _IncompleteTradeRef:
            incomplete.append(to_jsonable(dict(item)))
    return tuple(refs), tuple(incomplete), tuple(errors)


def _trade_refs_from_item(item: dict[str, Any]) -> tuple[ExecutedTradeRef, ...]:
    operation = str(item.get("operation") or "").strip()
    if not operation:
        raise _IncompleteTradeRef("operation is required")
    if operation == "close_all":
        closed = item.get("closed_orders") or []
        if not isinstance(closed, (list, tuple)):
            raise _IncompleteTradeRef("closed_orders must be a list")
        executed = _is_explicitly_executed(item)
        reject_code = _reject_code(item, executed)
        refs: list[ExecutedTradeRef] = []
        for order in closed:
            if not isinstance(order, dict):
                raise _IncompleteTradeRef("closed_orders entries must be objects")
            refs.append(
                _trade_ref(
                    {
                        "operation": "close",
                        "symbol": order.get("symbol"),
                        "market": order.get("market"),
                        "order_id": order.get("order_id"),
                        "trade_id": order.get("trade_id"),
                        "executed": executed,
                        "reject_code": reject_code,
                    }
                )
            )
        return tuple(refs)
    return (_trade_ref(item),)


def _trade_ref(item: Mapping[str, Any]) -> ExecutedTradeRef:
    operation = str(item.get("operation") or "").strip()
    symbol = str(item.get("symbol") or "").strip()
    if not operation or not symbol:
        raise _IncompleteTradeRef("operation and symbol are required")
    market = _parse_market(item.get("market"))
    executed = _is_explicitly_executed(item)
    return ExecutedTradeRef(
        operation=operation,
        symbol=symbol,
        market=market,
        order_id=_positive_int_or_none(item.get("order_id")),
        trade_id=_positive_int_or_none(item.get("trade_id")),
        executed=executed,
        reject_code=_reject_code(item, executed),
    )


def _reject_code(item: Mapping[str, Any], executed: bool) -> str | None:
    raw = item.get("reject_code")
    if isinstance(raw, str) and raw.strip():
        return raw
    if not executed and item.get("error") is not None:
        return "TRADE_REJECTED"
    return None


def _is_explicitly_executed(item: Mapping[str, Any]) -> bool:
    return item.get("executed") is True and item.get("error") is None


def _parse_market(value: Any) -> Market:
    if value is None:
        raise _IncompleteTradeRef("market is required")
    text = str(value).upper()
    if text == "US":
        return Market.US
    if text == "CRYPTO":
        return Market.CRYPTO
    raise _IncompleteTradeRef(f"unknown market: {value!r}")


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _number(value: Decimal | int | float) -> float:
    return float(value)


class _IncompleteTradeRef(ValueError):
    """Legacy execute_trade payload cannot be represented as ExecutedTradeRef."""


__all__ = [
    "executed_trades_from_legacy",
    "nested_executed_trades_from_legacy",
    "portfolio_from_context",
    "prices_from_context",
    "termination_from_legacy_decision",
]
