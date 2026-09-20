import logging
import math
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional

from sqlalchemy.orm import Session

from database.models import Account, Position, AIDecisionLog
from services.asset_calculator import calc_positions_value
from services.market_data import get_market_status, get_trading_price as get_last_price
from services.order_executor_leverage import place_and_execute_crypto
from services.order_matching import create_order, check_and_execute_order

if TYPE_CHECKING:
    from benchmark.application.trading import TradeCommandGateway


logger = logging.getLogger(__name__)

SUPPORTED_CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"}
SUPPORTED_US_SYMBOLS = {
    "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "PG", "JNJ", "UNH", "JPM", "V", "BA", "XOM", "NEE", "AMT", "PLD", "LIN"
}


def _parse_float_loose(value: Any) -> Optional[float]:
    """Extract the first valid float from numeric or loose text input."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value.strip())
        if m:
            try:
                parsed = float(m.group(0))
                return parsed if math.isfinite(parsed) else None
            except ValueError:
                return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _parse_int_loose(value: Any, *, default: int = 1) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value.strip())
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def execute_trade_tool(
    db: Session,
    account_id: int,
    operation: str,
    symbol: Optional[str] = None,
    market: str = "CRYPTO",
    direction: str = "long",
    size_mode: str = "portion",
    target_portion_of_balance: Optional[float] = None,
    usd_amount: Optional[float] = None,
    close_ratio: Optional[float] = None,
    leverage: int = 1,
    reason: str = "",
    idempotency_key: Optional[str] = None,
    decision_round_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    gateway: "TradeCommandGateway | None" = None,
) -> Dict[str, Any]:
    """Execute one trading action through the TradeCommandGateway."""
    from decimal import Decimal

    from benchmark.application.trading import get_default_trade_gateway
    from benchmark.contracts import Market, TradeCommand, to_jsonable

    normalized_size_mode = (size_mode or "portion").strip().lower()
    sizing_value = None
    if normalized_size_mode == "usd" and usd_amount is not None:
        sizing_value = Decimal(str(usd_amount))
    elif (operation or "").strip().lower() == "close" and close_ratio is not None:
        normalized_size_mode = "close_ratio"
        sizing_value = Decimal(str(close_ratio))
    elif target_portion_of_balance is not None:
        sizing_value = Decimal(str(target_portion_of_balance))

    try:
        market_text = (market or "CRYPTO").strip().upper()
        if market_text in {"STOCK", "STOCKS"}:
            market_text = "US"
        if market_text == "HYPERLIQUID":
            market_text = "CRYPTO"
        market_norm = Market(market_text)
        normalized_operation = (operation or "").strip().lower()
        normalized_symbol = (symbol or "").strip().upper()
        normalized_direction = (direction or "long").strip().lower()
        normalized_leverage = _parse_int_loose(leverage, default=1)
        if normalized_operation in {"hold", "close_all", "all_in"}:
            normalized_size_mode = None
            sizing_value = None
        if idempotency_key:
            normalized_idempotency_key = idempotency_key
        elif decision_round_id and tool_call_id:
            normalized_idempotency_key = f"{decision_round_id}:{tool_call_id}"
        else:
            return {
                "executed": False,
                "error": (
                    "A stable idempotency_key, or both decision_round_id and "
                    "tool_call_id, is required"
                ),
                "reject_code": "IDEMPOTENCY_KEY_REQUIRED",
            }
        command = TradeCommand(
            account_id=account_id,
            operation=normalized_operation,
            market=market_norm,
            symbol=normalized_symbol,
            direction=normalized_direction,
            sizing_mode=normalized_size_mode,
            sizing_value=sizing_value,
            leverage=normalized_leverage,
            reason=reason or "",
            idempotency_key=normalized_idempotency_key,
        )
        active_gateway = gateway or get_default_trade_gateway()
        gateway_result = active_gateway.execute(command)
        expire_all = getattr(db, "expire_all", None)
        if callable(expire_all):
            try:
                expire_all()
            except Exception as exc:
                from benchmark.contracts.errors import TradeGatewayError

                raise TradeGatewayError(
                    "Trade committed but caller session refresh failed",
                    code="TRADE_CALLER_SESSION_REFRESH_FAILED",
                    details={"error_type": type(exc).__name__},
                ) from exc
        # DTO raw_result is a deep-frozen JsonValue snapshot (lists → tuples).
        # Materialize back to mutable JSON-native containers for the legacy
        # agent-tool return shape (e.g. closed_orders: []).
        materialized = to_jsonable(gateway_result.raw_result)
        if not isinstance(materialized, dict):
            raise TypeError("TradeCommandResult.raw_result must be a JSON object")
        result = materialized
        if gateway_result.accepted:
            result.setdefault("executed", gateway_result.executed)
            result.setdefault("operation", gateway_result.normalized_command.operation)
            if gateway_result.normalized_command.symbol:
                result.setdefault("symbol", gateway_result.normalized_command.symbol)
            if not gateway_result.raw_result:
                result.setdefault("market", gateway_result.normalized_command.market.value)
                if gateway_result.normalized_command.direction:
                    result.setdefault("direction", gateway_result.normalized_command.direction)
            if gateway_result.order_id is not None:
                result.setdefault("order_id", gateway_result.order_id)
            if gateway_result.trade_id is not None:
                result.setdefault("trade_id", gateway_result.trade_id)
            return result
        result.setdefault("executed", False)
        result.setdefault("error", gateway_result.reject_message)
        result.setdefault("reject_code", gateway_result.reject_code)
        result.setdefault("operation", gateway_result.normalized_command.operation)
        if gateway_result.normalized_command.symbol:
            result.setdefault("symbol", gateway_result.normalized_command.symbol)
        if not gateway_result.raw_result:
            result.setdefault("market", gateway_result.normalized_command.market.value)
        return result
    except (TypeError, ValueError) as exc:
        return {
            "executed": False,
            "error": str(exc),
            "reject_code": "TRADE_COMMAND_INVALID",
        }


def _execute_trade_tool_legacy(
    db: Session,
    account_id: int,
    operation: str,
    symbol: Optional[str] = None,
    market: str = "CRYPTO",
    direction: str = "long",
    size_mode: str = "portion",
    target_portion_of_balance: Optional[float] = None,
    usd_amount: Optional[float] = None,
    close_ratio: Optional[float] = None,
    leverage: int = 1,
    reason: str = "",
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
) -> Dict[str, Any]:
    """
    Execute one trading action immediately.
    Supports:
    - open/close/hold
    - quick actions: all_in / close_all
    - sizing by portion or usd
    """
    try:
        operation = (operation or "").strip().lower()
        market = (market or "CRYPTO").strip().upper()
        direction = (direction or "long").strip().lower()
        size_mode = (size_mode or "portion").strip().lower()
        symbol = (symbol or "").strip().upper()
        leverage = _parse_int_loose(leverage, default=1)
        leverage = max(1, min(leverage, 10))

        if target_portion_of_balance is not None:
            coerced = _parse_float_loose(target_portion_of_balance)
            if coerced is None:
                return {
                    "executed": False,
                    "error": (
                        f"Invalid target_portion_of_balance: expected a number, got {target_portion_of_balance!r}. "
                        "Use JSON tool arguments only (no XML)."
                    ),
                }
            target_portion_of_balance = coerced
        if usd_amount is not None:
            coerced = _parse_float_loose(usd_amount)
            if coerced is None:
                return {
                    "executed": False,
                    "error": f"Invalid usd_amount: expected a number, got {usd_amount!r}. Use JSON tool arguments only.",
                }
            usd_amount = coerced
        if close_ratio is not None:
            coerced = _parse_float_loose(close_ratio)
            if coerced is None:
                return {
                    "executed": False,
                    "error": f"Invalid close_ratio: expected a number, got {close_ratio!r}. Use JSON tool arguments only.",
                }
            close_ratio = coerced

        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return {"executed": False, "error": f"Account {account_id} not found"}

        if operation == "hold":
            _save_trade_log(
                db=db,
                account=account,
                operation="hold",
                symbol=symbol or "",
                target_portion=0.0,
                reason=reason,
                executed=False,
                order_id=None,
                leverage=1,
                manage_transaction=manage_transaction,
                raise_on_error=raise_on_error,
            )
            if manage_transaction:
                db.commit()
            else:
                db.flush()
            return {
                "executed": True,
                "operation": "hold",
                "symbol": symbol,
                "market": market,
                "direction": direction,
                "message": "No trade executed (hold).",
            }

        if operation == "all_in":
            operation = "open"
            size_mode = "all_in"

        if operation == "close_all":
            return _handle_close_all(
                db=db,
                account=account,
                symbol=symbol,
                market=market,
                reason=reason,
                manage_transaction=manage_transaction,
                raise_on_error=raise_on_error,
            )

        if operation not in {"open", "close"}:
            return {"executed": False, "error": f"Unsupported operation: {operation}"}

        if size_mode not in {"portion", "usd", "all_in", "close_all"}:
            return {"executed": False, "error": f"Unsupported size_mode: {size_mode}"}

        # close_all is a close-only sizing intent; reject ambiguous open calls explicitly.
        if operation == "open" and size_mode == "close_all":
            return {
                "executed": False,
                "error": "size_mode=close_all is only valid for closing positions",
            }

        if direction not in {"long", "short"}:
            return {"executed": False, "error": "direction must be long or short"}

        if market not in {"CRYPTO", "US"}:
            return {"executed": False, "error": "market must be CRYPTO or US"}

        if not symbol:
            return {"executed": False, "error": "symbol is required for open/close"}

        symbol_check = symbol.upper()
        if market == "CRYPTO" and symbol_check not in SUPPORTED_CRYPTO_SYMBOLS:
            return {"executed": False, "error": f"Unsupported CRYPTO symbol: {symbol}"}
        if market == "US" and symbol_check not in SUPPORTED_US_SYMBOLS:
            return {"executed": False, "error": f"Unsupported US symbol: {symbol}"}

        if market == "US":
            status = get_market_status(symbol, "US")
            if not status.get("is_trading", False):
                return {"executed": False, "error": f"US market is closed for {symbol}"}
            leverage = 1

        price = float(get_last_price(symbol, market))
        if not math.isfinite(price) or price <= 0:
            return {"executed": False, "error": f"Invalid price for {symbol}"}

        if operation == "open":
            if market == "CRYPTO" and direction == "short" and leverage <= 1:
                return {
                    "executed": False,
                    "error": "CRYPTO short requires leverage > 1 (spot mode does not support SHORT).",
                }

            if market == "CRYPTO":
                existing_position = (
                    db.query(Position)
                    .filter(
                        Position.account_id == account.id,
                        Position.symbol == symbol,
                        Position.market == market,
                    )
                    .first()
                )
                if existing_position and float(existing_position.quantity or 0) > 0:
                    existing_side = (existing_position.side or "").strip().upper()
                    requested_side = "LONG" if direction == "long" else "SHORT"

                    if not existing_side:
                        return {
                            "executed": False,
                            "error": (
                                f"Existing position for {symbol} has no side metadata. "
                                "Please close it first before opening a new directional position."
                            ),
                        }

                    if existing_side != requested_side:
                        return {
                            "executed": False,
                            "error": (
                                f"Cannot open {requested_side} while holding {existing_side} "
                                f"on {symbol}. Please close the existing position first."
                            ),
                        }

                    existing_leverage = int(getattr(existing_position, "leverage", 1) or 1)
                    if existing_leverage != leverage:
                        return {
                            "executed": False,
                            "error": (
                                "Cannot add to position with different leverage. "
                                f"Existing: {existing_leverage}x, requested: {leverage}x"
                            ),
                        }

            quantity, notional = _calc_open_size(
                account=account,
                price=price,
                market=market,
                size_mode=size_mode,
                target_portion_of_balance=target_portion_of_balance,
                usd_amount=usd_amount,
            )
            if quantity <= 0:
                return {"executed": False, "error": "Calculated quantity <= 0"}

            order = _execute_open(
                db=db,
                account=account,
                symbol=symbol,
                market=market,
                direction=direction,
                quantity=quantity,
                leverage=leverage,
                manage_transaction=manage_transaction,
                raise_on_error=raise_on_error,
            )
            _save_trade_log(
                db=db,
                account=account,
                operation="open",
                symbol=symbol,
                target_portion=_safe_target_portion(account.id, db, notional),
                reason=reason or f"execute_trade open {symbol}",
                executed=True,
                order_id=order.id,
                leverage=leverage,
                manage_transaction=manage_transaction,
                raise_on_error=raise_on_error,
            )
            return {
                "executed": True,
                "operation": "open",
                "symbol": symbol,
                "market": market,
                "direction": direction,
                "quantity": quantity,
                "notional_usd": round(notional, 4),
                "order_id": order.id,
                "order_no": getattr(order, "order_no", None),
                "size_mode": size_mode,
            }

        # close
        position = (
            db.query(Position)
            .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == market)
            .first()
        )
        if not position or float(position.quantity) <= 0:
            return {"executed": False, "error": f"No position to close for {symbol}"}

        position_side = (position.side or "LONG").lower()
        if position_side not in {"long", "short"}:
            if raise_on_error:
                raise ValueError(
                    f"Position {symbol} has invalid side metadata: {position.side!r}"
                )
            position_side = "long"

        # If caller omitted direction/typed wrong, default to existing side to maximize close success.
        if direction != position_side:
            if raise_on_error:
                raise ValueError(
                    f"Close direction {direction} does not match position side {position_side}"
                )
            direction = position_side

        quantity, notional = _calc_close_size(
            position=position,
            market=market,
            price=price,
            size_mode=size_mode,
            target_portion_of_balance=target_portion_of_balance,
            usd_amount=usd_amount,
            close_ratio=close_ratio,
        )
        if quantity <= 0:
            return {"executed": False, "error": "Calculated close quantity <= 0"}

        order = _execute_close(
            db=db,
            account=account,
            symbol=symbol,
            market=market,
            direction=direction,
            quantity=quantity,
            position=position,
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        )
        _save_trade_log(
            db=db,
            account=account,
            operation="close",
            symbol=symbol,
            target_portion=0.0,
            reason=reason or f"execute_trade close {symbol}",
            executed=True,
            order_id=order.id,
            leverage=int(getattr(position, "leverage", 1) or 1),
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        )
        return {
            "executed": True,
            "operation": "close",
            "symbol": symbol,
            "market": market,
            "direction": direction,
            "quantity": quantity,
            "notional_usd": round(notional, 4),
            "order_id": order.id,
            "order_no": getattr(order, "order_no", None),
            "size_mode": size_mode,
        }
    except Exception as e:
        if raise_on_error:
            raise
        logger.error(f"execute_trade_tool failed: {e}", exc_info=True)
        return {"executed": False, "error": str(e)}


def _calc_open_size(
    account: Account,
    price: float,
    market: str,
    size_mode: str,
    target_portion_of_balance: Optional[float],
    usd_amount: Optional[float],
) -> tuple[float, float]:
    available_cash = float(account.current_cash)
    if not math.isfinite(available_cash):
        raise ValueError("account cash must be finite")
    if not math.isfinite(float(price)) or float(price) <= 0:
        raise ValueError("price must be finite and positive")
    if available_cash <= 0:
        return 0.0, 0.0

    if size_mode == "all_in":
        notional = available_cash
    elif size_mode == "usd":
        amt = float(usd_amount or 0.0)
        if not math.isfinite(amt):
            raise ValueError("usd_amount must be finite")
        notional = max(0.0, min(amt, available_cash))
    else:
        portion = float(target_portion_of_balance if target_portion_of_balance is not None else 0.0)
        if not math.isfinite(portion):
            raise ValueError("target_portion_of_balance must be finite")
        portion = max(0.0, min(portion, 1.0))
        notional = available_cash * portion

    if notional <= 0:
        return 0.0, 0.0

    if market == "US":
        qty = int(Decimal(str(notional)) / Decimal(str(price)))
    else:
        qty = float(Decimal(str(notional)) / Decimal(str(price)))
        qty = round(qty, 6)
    return float(qty), float(notional)


def _calc_close_size(
    position: Position,
    market: str,
    price: float,
    size_mode: str,
    target_portion_of_balance: Optional[float],
    usd_amount: Optional[float],
    close_ratio: Optional[float],
) -> tuple[float, float]:
    if float(position.leverage or 1) > 1:
        position_qty = float(position.quantity)
    else:
        position_qty = float(position.available_quantity)
    if not math.isfinite(position_qty):
        raise ValueError("position quantity must be finite")
    if not math.isfinite(float(price)) or float(price) <= 0:
        raise ValueError("price must be finite and positive")
    if position_qty <= 0:
        return 0.0, 0.0

    if size_mode in {"close_all", "all_in"}:
        qty = position_qty
    elif size_mode == "usd":
        amt = float(usd_amount or 0.0)
        if not math.isfinite(amt):
            raise ValueError("usd_amount must be finite")
        amt = max(0.0, amt)
        qty = amt / price if price > 0 else 0.0
    elif close_ratio is not None:
        ratio = float(close_ratio)
        if not math.isfinite(ratio):
            raise ValueError("close_ratio must be finite")
        ratio = max(0.0, min(ratio, 1.0))
        qty = position_qty * ratio
    else:
        ratio = float(target_portion_of_balance or 0.0)
        if not math.isfinite(ratio):
            raise ValueError("target_portion_of_balance must be finite")
        ratio = max(0.0, min(ratio, 1.0))
        qty = position_qty * ratio

    qty = min(qty, position_qty)
    if market == "US":
        qty = int(qty)
    else:
        qty = round(float(qty), 6)

    return float(qty), float(qty * price)


def _execute_open(
    db: Session,
    account: Account,
    symbol: str,
    market: str,
    direction: str,
    quantity: float,
    leverage: int,
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
):
    side = ("BUY" if direction == "long" else "SELL") if market == "US" else ("LONG" if direction == "long" else "SHORT")
    if market == "US":
        order = create_order(
            db=db,
            account=account,
            symbol=symbol,
            name=symbol,
            side=side,
            order_type="MARKET",
            price=None,
            quantity=quantity,
            leverage=1,
            market="US",
        )
        if manage_transaction:
            db.commit()
            db.refresh(order)
        else:
            db.flush()
        if not check_and_execute_order(
            db,
            order,
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        ):
            raise ValueError("US stock order was not executed")
        return order

    return place_and_execute_crypto(
        db=db,
        account_id=account.id,
        symbol=symbol,
        name=symbol,
        side=side,
        order_type="MARKET",
        price=None,
        quantity=quantity,
        leverage=leverage,
        manage_transaction=manage_transaction,
    )


def _execute_close(
    db: Session,
    account: Account,
    symbol: str,
    market: str,
    direction: str,
    quantity: float,
    position: Optional[Position] = None,
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
):
    side = "SELL" if direction == "long" else "BUY"
    if market == "US":
        order = create_order(
            db=db,
            account=account,
            symbol=symbol,
            name=symbol,
            side=side,
            order_type="MARKET",
            price=None,
            quantity=quantity,
            leverage=1,
            market="US",
        )
        if manage_transaction:
            db.commit()
            db.refresh(order)
        else:
            db.flush()
        if not check_and_execute_order(
            db,
            order,
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        ):
            raise ValueError("US stock close order was not executed")
        return order

    if position is not None and int(getattr(position, "leverage", 1) or 1) <= 1:
        # Align with place_and_execute_crypto: spot close can only SELL.
        side = "SELL"

    return place_and_execute_crypto(
        db=db,
        account_id=account.id,
        symbol=symbol,
        name=symbol,
        side=side,
        order_type="MARKET",
        price=None,
        quantity=quantity,
        leverage=1,
        manage_transaction=manage_transaction,
    )


def _handle_close_all(
    db: Session,
    account: Account,
    symbol: str,
    market: str,
    reason: str,
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
) -> Dict[str, Any]:
    query = db.query(Position).filter(Position.account_id == account.id)
    if symbol:
        query = query.filter(Position.symbol == symbol)
    if market in {"CRYPTO", "US"} and symbol:
        query = query.filter(Position.market == market)

    positions = [p for p in query.all() if float(p.quantity) > 0]
    if not positions:
        return {"executed": True, "operation": "close_all", "closed_orders": [], "message": "No positions to close."}

    closed_orders = []
    for pos in positions:
        side = (pos.side or "LONG").lower()
        if side not in {"long", "short"}:
            side = "long"
        qty = float(pos.quantity) if float(pos.leverage or 1) > 1 else float(pos.available_quantity)
        if pos.market == "US":
            qty = int(qty)
        else:
            qty = round(qty, 6)
        if qty <= 0:
            continue

        order = _execute_close(
            db=db,
            account=account,
            symbol=pos.symbol,
            market=pos.market,
            direction=side,
            quantity=qty,
            position=pos,
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        )
        closed_orders.append({"symbol": pos.symbol, "market": pos.market, "order_id": order.id, "quantity": qty})
        _save_trade_log(
            db=db,
            account=account,
            operation="close",
            symbol=pos.symbol,
            target_portion=0.0,
            reason=reason or "execute_trade close_all",
            executed=True,
            order_id=order.id,
            leverage=int(pos.leverage or 1),
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        )

    return {"executed": True, "operation": "close_all", "closed_orders": closed_orders, "count": len(closed_orders)}


def _safe_target_portion(account_id: int, db: Session, notional: float) -> float:
    total_assets = _calc_total_assets(db, account_id)
    if total_assets <= 0:
        return 0.0
    return float(max(0.0, min(notional / total_assets, 1.0)))


def _calc_total_assets(db: Session, account_id: int) -> float:
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        return 0.0
    return float(account.current_cash) + float(calc_positions_value(db, account_id))


def _save_trade_log(
    db: Session,
    account: Account,
    operation: str,
    symbol: str,
    target_portion: float,
    reason: str,
    executed: bool,
    order_id: Optional[int],
    leverage: int,
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
) -> None:
    try:
        total_assets = _calc_total_assets(db, account.id)
        row = AIDecisionLog(
            account_id=account.id,
            reason=reason or "",
            operation=operation,
            symbol=symbol or None,
            prev_portion=Decimal("0"),
            target_portion=Decimal(str(max(0.0, min(target_portion, 1.0)))),
            total_balance=Decimal(str(total_assets)),
            executed="true" if executed else "false",
            order_id=order_id,
            leverage=int(max(1, leverage)),
            trace_id=None,
        )
        db.add(row)
        if manage_transaction:
            db.commit()
        else:
            db.flush()
    except Exception as e:
        if manage_transaction:
            db.rollback()
        if raise_on_error:
            raise
        logger.warning(f"Failed to save execute_trade decision log: {e}")
