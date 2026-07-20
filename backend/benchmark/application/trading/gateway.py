"""Synchronous trade command gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from threading import Lock
from typing import Callable, Mapping, Any

from sqlalchemy.orm import Session

from benchmark.contracts import TradeCommand, TradeCommandResult

from .policy import normalize_trade_command


@dataclass
class TradeCommandGateway:
    db: Session
    executor: Callable[..., Mapping[str, Any]] | None = None
    _results: dict[tuple[int, str], TradeCommandResult] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def execute(self, command: TradeCommand) -> TradeCommandResult:
        normalized = normalize_trade_command(command)
        key = (normalized.account_id, normalized.idempotency_key)
        with self._lock:
            existing = self._results.get(key)
            if existing is not None:
                return existing

        result = self._execute_once(normalized)
        with self._lock:
            self._results.setdefault(key, result)
            return self._results[key]

    def _execute_once(self, command: TradeCommand) -> TradeCommandResult:
        try:
            executor = self.executor
            if executor is None:
                from services.agent.trade_execution_tool import _execute_trade_tool_legacy

                executor = _execute_trade_tool_legacy

            tool_result = executor(
                db=self.db,
                account_id=command.account_id,
                operation=command.operation,
                symbol=command.symbol,
                market=command.market.value,
                direction=command.direction or "long",
                size_mode="portion" if command.sizing_mode == "close_ratio" else (command.sizing_mode or "portion"),
                target_portion_of_balance=_as_float(command.sizing_value) if command.sizing_mode == "portion" else None,
                usd_amount=_as_float(command.sizing_value) if command.sizing_mode == "usd" else None,
                close_ratio=_as_float(command.sizing_value) if command.sizing_mode == "close_ratio" else None,
                leverage=command.leverage,
                reason=command.reason,
            )
        except Exception as exc:
            try:
                self.db.rollback()
            except Exception:
                pass
            return TradeCommandResult(False, False, "GATEWAY_ERROR", str(exc), None, None, command)

        if not isinstance(tool_result, Mapping):
            return TradeCommandResult(False, False, "INVALID_TOOL_RESULT", "trade executor returned invalid result", None, None, command)

        executed = bool(tool_result.get("executed"))
        order_id = _as_positive_int(tool_result.get("order_id"))
        trade_id = _as_positive_int(tool_result.get("trade_id"))
        if executed:
            return TradeCommandResult(True, True, None, None, order_id, trade_id, command)

        message = str(tool_result.get("error") or tool_result.get("message") or "trade command rejected")
        return TradeCommandResult(False, False, _reject_code(message), message, order_id, trade_id, command)


def _as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _as_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _reject_code(message: str) -> str:
    text = message.lower()
    if "price" in text:
        return "PRICE_UNAVAILABLE"
    if "unsupported" in text:
        return "UNSUPPORTED_COMMAND"
    if "market is closed" in text:
        return "MARKET_CLOSED"
    if "position" in text:
        return "POSITION_INVALID"
    if "symbol" in text:
        return "SYMBOL_INVALID"
    return "TRADE_REJECTED"


def get_default_trade_gateway(db: Session) -> TradeCommandGateway:
    return TradeCommandGateway(db=db)


__all__ = ["TradeCommandGateway", "get_default_trade_gateway"]
