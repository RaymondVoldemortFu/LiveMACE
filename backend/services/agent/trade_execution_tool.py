import logging
import re
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from database.models import Account, Position, AIDecisionLog
from services.asset_calculator import calc_positions_value
from services.market_data import get_last_price, get_market_status
from services.order_executor_leverage import place_and_execute_crypto
from services.order_matching import create_order, check_and_execute_order


logger = logging.getLogger(__name__)

SUPPORTED_CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"}
SUPPORTED_US_SYMBOLS = {
    "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "PG", "JNJ", "UNH", "JPM", "V", "BA", "XOM", "NEE", "AMT", "PLD", "LIN"
}


def _parse_float_loose(value: Any) -> Optional[float]:
    """从数字或杂糅 XML/文本中提取第一个合法 float；无法解析返回 None。"""
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
                return float(m.group(0))
            except ValueError:
                return None
    try:
        return float(value)
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
) -> Dict[str, Any]:
    """Execute one trading action through the TradeCommandGateway."""
    from decimal import Decimal

    from benchmark.application.trading import get_default_trade_gateway
    from benchmark.contracts import Market, TradeCommand

    normalized_size_mode = (size_mode or "portion").strip().lower()
    sizing_value = None
    if normalized_size_mode == "usd" and usd_amount is not None:
        sizing_value = Decimal(str(usd_amount))
    elif close_ratio is not None:
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
        command = TradeCommand(
            account_id=account_id,
            operation=(operation or "").strip().lower(),
            market=market_norm,
            symbol=(symbol or "").strip().upper(),
            direction=(direction or "long").strip().lower(),
            sizing_mode=normalized_size_mode,
            sizing_value=sizing_value,
            leverage=_parse_int_loose(leverage, default=1),
            reason=reason or "",
            idempotency_key=idempotency_key or f"tool:{account_id}:{operation}:{symbol}:{market}:{direction}:{normalized_size_mode}:{sizing_value}:{leverage}",
        )
        gateway_result = get_default_trade_gateway(db).execute(command)
        if gateway_result.accepted:
            return {
                "executed": gateway_result.executed,
                "operation": gateway_result.normalized_command.operation,
                "symbol": gateway_result.normalized_command.symbol,
                "market": gateway_result.normalized_command.market.value,
                "direction": gateway_result.normalized_command.direction,
                "order_id": gateway_result.order_id,
                "trade_id": gateway_result.trade_id,
            }
        return {
            "executed": False,
            "error": gateway_result.reject_message,
            "reject_code": gateway_result.reject_code,
            "operation": gateway_result.normalized_command.operation,
            "symbol": gateway_result.normalized_command.symbol,
            "market": gateway_result.normalized_command.market.value,
        }
    except Exception as exc:
        logger.error("execute_trade_tool gateway adapter failed: %s", exc, exc_info=True)
        return {"executed": False, "error": str(exc), "reject_code": "TRADE_GATEWAY_ERROR"}


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
            )
            db.commit()
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
            return _handle_close_all(db=db, account=account, symbol=symbol, market=market, reason=reason)

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
        if price <= 0:
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
            position_side = "long"

        # If caller omitted direction/typed wrong, default to existing side to maximize close success.
        if direction != position_side:
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
    if available_cash <= 0:
        return 0.0, 0.0

    if size_mode == "all_in":
        notional = available_cash
    elif size_mode == "usd":
        amt = float(usd_amount or 0.0)
        notional = max(0.0, min(amt, available_cash))
    else:
        portion = float(target_portion_of_balance if target_portion_of_balance is not None else 0.0)
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
    if position_qty <= 0:
        return 0.0, 0.0

    if size_mode in {"close_all", "all_in"}:
        qty = position_qty
    elif size_mode == "usd":
        amt = max(0.0, float(usd_amount or 0.0))
        qty = amt / price if price > 0 else 0.0
    elif close_ratio is not None:
        ratio = max(0.0, min(float(close_ratio), 1.0))
        qty = position_qty * ratio
    else:
        ratio = max(0.0, min(float(target_portion_of_balance or 0.0), 1.0))
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
        db.commit()
        db.refresh(order)
        if not check_and_execute_order(db, order):
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
    )


def _execute_close(
    db: Session,
    account: Account,
    symbol: str,
    market: str,
    direction: str,
    quantity: float,
    position: Optional[Position] = None,
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
        db.commit()
        db.refresh(order)
        if not check_and_execute_order(db, order):
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
    )


def _handle_close_all(db: Session, account: Account, symbol: str, market: str, reason: str) -> Dict[str, Any]:
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
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Failed to save execute_trade decision log: {e}")

