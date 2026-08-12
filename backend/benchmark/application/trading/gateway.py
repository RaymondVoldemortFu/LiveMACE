"""Reliable synchronous trading gateway with transactional idempotency."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from threading import Condition, Lock
from typing import Any, Protocol
from typing import runtime_checkable

from benchmark.contracts import Market, TradeCommand, TradeCommandResult, to_jsonable
from benchmark.contracts.errors import TradeGatewayError
from benchmark.persistence import (
    UnitOfWork,
    UnitOfWorkFactory,
    PersistenceConflictError,
    default_unit_of_work_factory,
)
from benchmark.persistence.trade_transactions import TradeTransactionOperations
from benchmark.infrastructure.market.symbols import resolve_symbol_market

from .commands import (
    CancelOrderCommand,
    CreateOrderCommand,
    OrderCommandResult,
    ProcessPendingOrders,
    ProcessingResult,
)
from .policy import normalize_trade_command


class TradeExecutor(Protocol):
    def __call__(
        self,
        transaction: TradeTransactionOperations,
        command: TradeCommand,
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class TradeCommandGateway(Protocol):
    def execute(self, command: TradeCommand) -> TradeCommandResult: ...


@dataclass
class SynchronousTradeCommandGateway:
    uow_factory: UnitOfWorkFactory
    executor: TradeExecutor | None = None
    _store: "TradeCommandIdempotencyStore" = field(
        default_factory=lambda: TradeCommandIdempotencyStore()
    )
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not callable(self.uow_factory):
            raise TypeError("uow_factory must be callable")
        if self.executor is not None and not callable(self.executor):
            raise TypeError("executor must be callable or None")

    def execute(self, command: TradeCommand) -> TradeCommandResult:
        """Execute exactly once per account/idempotency key.

        Policy errors and infrastructure failures are raised explicitly.
        Expected business rejects are returned as ``TradeCommandResult`` and
        persisted so a retry observes the identical result.
        """

        normalized = normalize_trade_command(command)
        key = (normalized.account_id, normalized.idempotency_key)
        state = self._store.start(key, _encode_command(normalized))
        if not state.should_execute:
            return self._store.wait(key)
        try:
            result = self._execute_durable(normalized)
        except BaseException:
            self._store.fail(key)
            raise
        self._store.complete(key, result)
        return result

    def create_order(self, command: CreateOrderCommand) -> OrderCommandResult:
        if not isinstance(command, CreateOrderCommand):
            raise TypeError("command must be CreateOrderCommand")
        try:
            with self.uow_factory() as uow:
                account = uow.accounts.get_for_update(command.account_id)
                if account is None:
                    uow.rollback()
                    return OrderCommandResult(
                        False,
                        reject_code="ACCOUNT_NOT_FOUND",
                        reject_message=f"Account {command.account_id} not found",
                    )
                resolved = resolve_symbol_market(command.symbol, command.market)

                order = uow.trade_operations.create_order(
                    account=account,
                    symbol=resolved.symbol,
                    name=resolved.symbol,
                    side=command.side,
                    order_type=command.order_type,
                    price=None if command.price is None else float(command.price),
                    quantity=float(command.quantity),
                    leverage=command.leverage,
                    market=command.market.value,
                )
                trade_id = None
                if command.order_type == "MARKET":
                    executed = uow.trade_operations.execute_order(order)
                    if not executed:
                        raise TradeGatewayError(
                            "MARKET order was not executed",
                            code="ORDER_NOT_EXECUTED",
                            details={"order_no": order.order_no},
                        )
                    trades = uow.trades.list_by_order(order.id)
                    if len(trades) != 1:
                        raise TradeGatewayError(
                            "Executed order did not produce exactly one trade",
                            code="TRADE_INVARIANT_VIOLATION",
                            details={
                                "order_id": order.id,
                                "trade_count": len(trades),
                            },
                        )
                    trade_id = trades[0].id
                uow.commit()
                return OrderCommandResult(True, order.id, trade_id)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except TradeGatewayError:
            raise
        except ValueError as exc:
            return OrderCommandResult(
                False,
                reject_code=_reject_code(str(exc)),
                reject_message=str(exc),
            )
        except Exception as exc:
            raise _unexpected_gateway_error("create_order", exc) from exc

    def cancel_order(self, command: CancelOrderCommand) -> OrderCommandResult:
        if not isinstance(command, CancelOrderCommand):
            raise TypeError("command must be CancelOrderCommand")
        try:
            with self.uow_factory() as uow:
                account = uow.accounts.get_for_update(command.account_id)
                if account is None:
                    uow.rollback()
                    return OrderCommandResult(
                        False,
                        reject_code="ACCOUNT_NOT_FOUND",
                        reject_message=f"Account {command.account_id} not found",
                    )
                order = uow.orders.get_by_no_for_update(command.order_no)
                if order is None or order.account_id != command.account_id:
                    uow.rollback()
                    return OrderCommandResult(
                        False,
                        reject_code="ORDER_NOT_FOUND",
                        reject_message="Order not found for account",
                    )
                cancelled = uow.trade_operations.cancel_order(
                    order, reason=command.reason
                )
                if not cancelled:
                    uow.rollback()
                    return OrderCommandResult(
                        False,
                        order_id=order.id,
                        reject_code="ORDER_NOT_CANCELLABLE",
                        reject_message=f"Order status is {order.status}",
                    )
                uow.commit()
                return OrderCommandResult(True, order_id=order.id)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except TradeGatewayError:
            raise
        except Exception as exc:
            raise _unexpected_gateway_error("cancel_order", exc) from exc

    def process_pending(self, command: ProcessPendingOrders) -> ProcessingResult:
        if not isinstance(command, ProcessPendingOrders):
            raise TypeError("command must be ProcessPendingOrders")
        try:
            if command.account_id is not None:
                return self._process_pending_for_account(command.account_id)

            # Discover only account ids in this short read transaction. Each
            # account is then processed in its own account-first transaction,
            # preventing a global batch from holding unrelated account locks.
            with self.uow_factory() as uow:
                account_ids = tuple(uow.orders.list_pending_account_ids())
                uow.rollback()
            processed = 0
            executed = 0
            for account_id in account_ids:
                result = self._process_pending_for_account(account_id)
                processed += result.processed
                executed += result.executed
            return ProcessingResult(processed=processed, executed=executed)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except TradeGatewayError:
            raise
        except Exception as exc:
            raise _unexpected_gateway_error("process_pending", exc) from exc

    def _process_pending_for_account(self, account_id: int) -> ProcessingResult:
        with self.uow_factory() as uow:
            account = uow.accounts.get_for_update(account_id)
            if account is None:
                uow.rollback()
                return ProcessingResult(processed=0, executed=0)
            pending = tuple(uow.orders.list_pending_for_update(account_id))
            executed = 0
            for order in pending:
                if uow.trade_operations.execute_order(order):
                    executed += 1
            uow.commit()
            return ProcessingResult(processed=len(pending), executed=executed)

    def _execute_durable(self, command: TradeCommand) -> TradeCommandResult:
        command_json = _encode_command(command)
        try:
            with self.uow_factory() as uow:
                account = uow.accounts.get_for_update(command.account_id)
                if account is None:
                    uow.rollback()
                    return TradeCommandResult(
                        False,
                        False,
                        "ACCOUNT_NOT_FOUND",
                        f"Account {command.account_id} not found",
                        None,
                        None,
                        command,
                    )
                existing = uow.trade_command_receipts.get(
                    command.account_id,
                    command.idempotency_key,
                )
                if existing is not None:
                    return _result_from_receipt(existing, command_json)
                try:
                    receipt = uow.trade_command_receipts.claim(
                        command.account_id,
                        command.idempotency_key,
                        command_json,
                    )
                except PersistenceConflictError:
                    uow.rollback()
                    return self._load_committed_receipt(command, command_json)

                with uow.trade_operations.savepoint() as business_transaction:
                    result = self._execute_once(uow.trade_operations, command)
                    if not result.accepted:
                        business_transaction.rollback()
                result_json = _encode_result(result)
                uow.trade_command_receipts.complete(
                    receipt,
                    result_json,
                    self.clock(),
                )
                uow.commit()
                return result
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except TradeGatewayError:
            raise
        except Exception as exc:
            raise _unexpected_gateway_error("execute", exc) from exc

    def _load_committed_receipt(
        self,
        command: TradeCommand,
        command_json: str,
    ) -> TradeCommandResult:
        with self.uow_factory() as uow:
            receipt = uow.trade_command_receipts.get(
                command.account_id,
                command.idempotency_key,
            )
            if receipt is None:
                raise TradeGatewayError(
                    "Idempotency conflict did not expose a committed receipt",
                    code="TRADE_IDEMPOTENCY_INDETERMINATE",
                    details={"account_id": command.account_id},
                )
            return _result_from_receipt(receipt, command_json)

    def _execute_once(
        self,
        transaction: TradeTransactionOperations,
        command: TradeCommand,
    ) -> TradeCommandResult:
        try:
            if self.executor is None:
                # The one legacy Session callback is invoked only inside the
                # infrastructure adapter. Application executors receive the
                # narrow transaction port itself.
                raw = transaction.run_legacy_executor(_execute_legacy, command)
            else:
                raw = self.executor(transaction, command)
        except ValueError as exc:
            message = str(exc)
            return TradeCommandResult(
                False,
                False,
                _reject_code(message),
                message,
                None,
                None,
                command,
            )
        if not isinstance(raw, Mapping):
            raise TradeGatewayError(
                "Trade executor returned a non-mapping result",
                code="INVALID_TRADE_EXECUTOR_RESULT",
            )
        raw_result = dict(raw)
        executed_value = raw.get("executed")
        if not isinstance(executed_value, bool):
            raise TradeGatewayError(
                "Trade executor result requires a boolean executed field",
                code="INVALID_TRADE_EXECUTOR_RESULT",
            )
        order_id = _positive_int_or_none(raw.get("order_id"), "order_id")
        trade_id = _positive_int_or_none(raw.get("trade_id"), "trade_id")
        if executed_value:
            return TradeCommandResult(
                True,
                True,
                None,
                None,
                order_id,
                trade_id,
                command,
                raw_result,
            )
        message = raw.get("error") or raw.get("message")
        if not isinstance(message, str) or not message.strip():
            raise TradeGatewayError(
                "Rejected trade executor result requires an error message",
                code="INVALID_TRADE_EXECUTOR_RESULT",
            )
        return TradeCommandResult(
            False,
            False,
            _reject_code(message),
            message,
            order_id,
            trade_id,
            command,
            raw_result,
        )


def _execute_legacy(session: Any, command: TradeCommand) -> Mapping[str, Any]:
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
            if command.operation == "all_in"
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

def _encode_command(command: TradeCommand) -> str:
    return json.dumps(
        to_jsonable(command),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _encode_result(result: TradeCommandResult) -> str:
    try:
        value = to_jsonable(result)
    except (TypeError, ValueError) as exc:
        raise TradeGatewayError(
            "Trade result is not JSON-compatible",
            code="INVALID_TRADE_EXECUTOR_RESULT",
        ) from exc
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _result_from_receipt(receipt: Any, expected_command_json: str) -> TradeCommandResult:
    if receipt.command_json != expected_command_json:
        raise TradeGatewayError(
            "Idempotency key was reused for a different trade command",
            code="TRADE_IDEMPOTENCY_KEY_REUSED",
            details={"account_id": receipt.account_id},
        )
    if receipt.status != "COMPLETED" or not receipt.result_json:
        raise TradeGatewayError(
            "Trade command receipt is not complete",
            code="TRADE_IDEMPOTENCY_IN_PROGRESS",
            details={"account_id": receipt.account_id},
        )
    try:
        raw = json.loads(receipt.result_json)
        command_raw = raw["normalized_command"]
        command = TradeCommand(
            account_id=command_raw["account_id"],
            operation=command_raw["operation"],
            market=Market(command_raw["market"]),
            symbol=command_raw["symbol"],
            direction=command_raw["direction"],
            sizing_mode=command_raw["sizing_mode"],
            sizing_value=(
                None
                if command_raw["sizing_value"] is None
                else Decimal(command_raw["sizing_value"])
            ),
            leverage=command_raw["leverage"],
            reason=command_raw["reason"],
            idempotency_key=command_raw["idempotency_key"],
        )
        return TradeCommandResult(
            accepted=raw["accepted"],
            executed=raw["executed"],
            reject_code=raw["reject_code"],
            reject_message=raw["reject_message"],
            order_id=raw["order_id"],
            trade_id=raw["trade_id"],
            normalized_command=command,
            raw_result=raw["raw_result"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TradeGatewayError(
            "Stored trade command receipt is corrupt",
            code="TRADE_RECEIPT_CORRUPT",
            details={"account_id": receipt.account_id},
        ) from exc


def _as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _positive_int_or_none(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TradeGatewayError(
            f"Trade executor returned an invalid {name}",
            code="INVALID_TRADE_EXECUTOR_RESULT",
        )
    return value


def _reject_code(message: str) -> str:
    text = message.lower()
    if "account" in text and "not found" in text:
        return "ACCOUNT_NOT_FOUND"
    if "market is closed" in text:
        return "MARKET_CLOSED"
    if "price" in text:
        return "PRICE_UNAVAILABLE"
    if "insufficient cash" in text:
        return "INSUFFICIENT_CASH"
    if "position" in text:
        return "POSITION_INVALID"
    if "symbol" in text:
        return "SYMBOL_INVALID"
    if "leverage" in text:
        return "LEVERAGE_INVALID"
    if "unsupported" in text:
        return "UNSUPPORTED_COMMAND"
    return "TRADE_REJECTED"


def _unexpected_gateway_error(operation: str, exc: Exception) -> TradeGatewayError:
    return TradeGatewayError(
        f"Trade gateway operation failed: {operation}",
        code="TRADE_GATEWAY_INFRASTRUCTURE_ERROR",
        details={"operation": operation, "error_type": type(exc).__name__},
    )


@dataclass(frozen=True)
class _IdempotencyStart:
    should_execute: bool


class TradeCommandIdempotencyStore:
    """Process-local coordination; durable correctness comes from DB receipts."""

    def __init__(self) -> None:
        self._results: dict[tuple[int, str], TradeCommandResult] = {}
        self._fingerprints: dict[tuple[int, str], str] = {}
        self._in_flight: set[tuple[int, str]] = set()
        self._condition = Condition(Lock())

    def start(self, key: tuple[int, str], command_fingerprint: str) -> _IdempotencyStart:
        with self._condition:
            existing_fingerprint = self._fingerprints.get(key)
            if (
                existing_fingerprint is not None
                and existing_fingerprint != command_fingerprint
            ):
                raise TradeGatewayError(
                    "Idempotency key was reused for a different trade command",
                    code="TRADE_IDEMPOTENCY_KEY_REUSED",
                    details={"account_id": key[0]},
                )
            if key in self._results or key in self._in_flight:
                return _IdempotencyStart(False)
            self._fingerprints[key] = command_fingerprint
            self._in_flight.add(key)
            return _IdempotencyStart(True)

    def wait(self, key: tuple[int, str]) -> TradeCommandResult:
        with self._condition:
            while key in self._in_flight:
                self._condition.wait()
            result = self._results.get(key)
            if result is None:
                raise TradeGatewayError(
                    "Concurrent trade command failed before producing a result",
                    code="TRADE_IDEMPOTENCY_PREVIOUS_ATTEMPT_FAILED",
                )
            return result

    def complete(self, key: tuple[int, str], result: TradeCommandResult) -> None:
        with self._condition:
            self._results.setdefault(key, result)
            self._in_flight.discard(key)
            self._condition.notify_all()

    def fail(self, key: tuple[int, str]) -> None:
        with self._condition:
            self._in_flight.discard(key)
            self._fingerprints.pop(key, None)
            self._condition.notify_all()

    def clear(self) -> None:
        with self._condition:
            self._results.clear()
            self._fingerprints.clear()
            self._in_flight.clear()
            self._condition.notify_all()


def get_default_trade_gateway() -> TradeCommandGateway:
    """Create a Gateway that owns a fresh UoW for every command."""

    return SynchronousTradeCommandGateway(
        uow_factory=default_unit_of_work_factory(),
    )


__all__ = [
    "TradeCommandGateway",
    "TradeCommandIdempotencyStore",
    "TradeExecutor",
    "SynchronousTradeCommandGateway",
    "get_default_trade_gateway",
]
