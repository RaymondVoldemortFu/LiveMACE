"""Pure settlement planners: detached inputs in, ledger changes out.

These functions preserve the established fee, sizing, margin and interest
formulas. They neither read a database nor perform IO. The caller supplies the
quote and clock once; only the repository applies a successful plan.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Mapping, Any, Optional
import math
import datetime as datetime_module

from .fees import (
    CRYPTO_MIN_COMMISSION,
    CRYPTO_COMMISSION_RATE,
    CRYPTO_TAKER_FEE_RATE,
    CRYPTO_INTEREST_RATE_HOURLY,
    CRYPTO_MAX_LEVERAGE,
    CRYPTO_MIN_ORDER_QUANTITY,
)


@dataclass(frozen=True)
class LedgerPlan:
    """Computed account/position/order values plus exactly one fill record."""

    account: Mapping[str, Any]
    position: Mapping[str, Any] | None
    order: Mapping[str, Any]
    trade: Mapping[str, Any] | None


def _copy(values):
    return None if values is None else SimpleNamespace(**deepcopy(dict(values)))


def _position(**values):
    return SimpleNamespace(
        **{**dict(accumulated_interest=0, last_interest_time=None, side=None), **values}
    )


def _result(account, position, order, trade):
    return LedgerPlan(
        vars(account), vars(position) if position else None, vars(order), trade
    )


def crypto_fee(notional: Decimal, leverage: int = 1) -> Decimal:
    return notional * Decimal(str(CRYPTO_TAKER_FEE_RATE)) * 2


def commission_for(notional: Decimal) -> Decimal:
    return max(
        notional * Decimal(str(CRYPTO_COMMISSION_RATE)),
        Decimal(str(CRYPTO_MIN_COMMISSION)),
    )


def position_interest(position, now: datetime) -> Decimal:
    if not position.last_interest_time or position.leverage <= 1:
        return Decimal(0)
    last_time = position.last_interest_time
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=datetime_module.timezone.utc)
    hours_elapsed = (now - last_time).total_seconds() / 3600
    borrowed_notional = (
        Decimal(str(position.quantity))
        * Decimal(str(position.avg_cost))
        * (Decimal(position.leverage) - 1)
        / Decimal(position.leverage)
    )
    return (
        borrowed_notional
        * Decimal(str(CRYPTO_INTEREST_RATE_HOURLY))
        * Decimal(str(hours_elapsed))
    )


def release_frozen(account, order, execution_price, commission):
    if order.side == "BUY":
        notional = execution_price * Decimal(order.quantity)
        account.frozen_cash = float(
            max(Decimal(str(account.frozen_cash)) - notional - commission, Decimal("0"))
        )


def plan_crypto(
    account_values,
    position_values,
    order_values,
    *,
    side: str,
    quantity: float,
    leverage: int,
    exec_price: Decimal,
    now: datetime,
) -> LedgerPlan:
    """Plan one crypto settlement on copies; rejection leaves inputs unchanged."""
    account, pos, order = (
        _copy(account_values),
        _copy(position_values),
        _copy(order_values),
    )
    symbol, name = order.symbol, order.name
    if leverage < 1 or leverage > CRYPTO_MAX_LEVERAGE:
        raise ValueError(f"Leverage must be between 1 and {CRYPTO_MAX_LEVERAGE}")
    if quantity < CRYPTO_MIN_ORDER_QUANTITY:
        raise ValueError(f"Quantity must be >= {CRYPTO_MIN_ORDER_QUANTITY}")
    if not exec_price.is_finite() or exec_price <= 0:
        raise ValueError("Market price must be positive and finite")
    notional = exec_price * Decimal(str(quantity))
    taker_fee = crypto_fee(notional, leverage)
    interest_charged = Decimal(0)

    # Handle different order sides
    if side.upper() in ("LONG", "SHORT"):
        # Opening or adding to an existing position
        side_upper = side.upper()
        has_active_position = bool(
            pos and pos.side and Decimal(str(pos.quantity or 0)) > 0
        )

        if has_active_position:
            if pos.side != side_upper:
                raise ValueError(
                    f"Cannot open {side_upper} position while holding {pos.side} position. Close existing position first."
                )

            existing_leverage = int(pos.leverage or 1)
            if existing_leverage != leverage:
                raise ValueError(
                    f"Cannot add to position with different leverage. Existing: {existing_leverage}x, requested: {leverage}x"
                )

            # For leveraged positions, settle accumulated interest before adding.
            if existing_leverage > 1:
                interest_charged = position_interest(pos, now)

        # Calculate margin required
        initial_margin = notional / Decimal(leverage)
        total_cost = initial_margin + taker_fee

        # Check if enough cash
        available_cash = Decimal(str(account.current_cash))
        required_cash = total_cost + interest_charged
        if available_cash < required_cash:
            raise ValueError(
                f"Insufficient cash. Need {required_cash}, have {available_cash}"
            )

        if interest_charged > 0:
            pos.accumulated_interest = float(
                Decimal(str(pos.accumulated_interest)) + interest_charged
            )

        # Deduct margin, fee and (if any) interest from cash
        account.current_cash = float(available_cash - required_cash)

        # Only track margin for leveraged positions (leverage > 1)
        if leverage > 1:
            account.margin_used = float(
                Decimal(str(account.margin_used)) + initial_margin
            )

        if has_active_position:
            # Adding to existing same-side/same-leverage position - weighted avg cost + quantity increment.
            old_notional = Decimal(str(pos.quantity)) * Decimal(str(pos.avg_cost))
            new_qty = Decimal(str(pos.quantity)) + Decimal(str(quantity))
            new_cost = (old_notional + notional) / new_qty
            pos.quantity = float(new_qty)
            pos.available_quantity = float(
                Decimal(str(pos.available_quantity or 0)) + Decimal(str(quantity))
            )
            pos.avg_cost = float(new_cost)
            pos.leverage = leverage
            pos.side = side_upper
        else:
            # Create new position or convert spot to leveraged
            if not pos:
                pos = _position(
                    version="v1",
                    account_id=account.id,
                    symbol=symbol,
                    name=name,
                    market="CRYPTO",
                    quantity=0,
                    available_quantity=0,
                    avg_cost=0,
                    leverage=1,
                )

            # Set leveraged position
            pos.quantity = quantity
            pos.available_quantity = quantity
            pos.avg_cost = float(exec_price)
            pos.leverage = leverage
            pos.side = side_upper

        # Update interest timestamp
        pos.last_interest_time = now

    elif side.upper() in ("BUY", "SELL"):
        # Closing a position (partial or full)
        if not pos or pos.quantity == 0:
            raise ValueError("No position to close")

        # Calculate interest before closing
        interest_charged = position_interest(pos, now)
        if interest_charged > 0:
            pos.accumulated_interest = float(
                Decimal(str(pos.accumulated_interest)) + interest_charged
            )
            # Closing releases collateral in this same transaction. Cash may
            # be below interest before settlement; that must not block risk reduction.
            account.current_cash = float(
                Decimal(str(account.current_cash)) - interest_charged
            )

        if pos.leverage > 1:
            # Closing leveraged position
            # BUY closes SHORT, SELL closes LONG
            if (side.upper() == "SELL" and pos.side != "LONG") or (
                side.upper() == "BUY" and pos.side != "SHORT"
            ):
                raise ValueError(f"Cannot {side} to close a {pos.side} position")

            if Decimal(str(quantity)) > Decimal(str(pos.quantity)):
                raise ValueError(
                    f"Cannot close more than position size. Position: {pos.quantity}, Trying to close: {quantity}"
                )

            # Calculate PnL
            entry_notional = Decimal(str(pos.avg_cost)) * Decimal(str(quantity))
            exit_notional = notional

            if pos.side == "LONG":
                pnl = exit_notional - entry_notional
            else:  # SHORT
                pnl = entry_notional - exit_notional

            # Release margin proportionally (only if leverage > 1)
            margin_released = entry_notional / Decimal(pos.leverage)

            # Net cash change = PnL + margin released - closing fee
            net_cash_change = pnl + margin_released - taker_fee
            account.current_cash = float(
                Decimal(str(account.current_cash)) + net_cash_change
            )

            # Only update margin_used for leveraged positions
            if pos.leverage > 1:
                account.margin_used = float(
                    Decimal(str(account.margin_used)) - margin_released
                )

            # Update position
            pos.quantity = float(Decimal(str(pos.quantity)) - Decimal(str(quantity)))
            pos.available_quantity = float(
                Decimal(str(pos.available_quantity)) - Decimal(str(quantity))
            )

            if pos.quantity == 0:
                pos.side = None
                pos.leverage = 1
                pos.last_interest_time = None
            else:
                pos.last_interest_time = now
        else:
            # Closing spot position (simple sell)
            if side.upper() != "SELL":
                raise ValueError("Can only SELL spot positions")

            if Decimal(str(quantity)) > Decimal(str(pos.available_quantity)):
                raise ValueError(
                    f"Insufficient position. Have: {pos.available_quantity}, Trying to sell: {quantity}"
                )

            # Simple spot sell
            cash_gain = notional - taker_fee
            account.current_cash = float(Decimal(str(account.current_cash)) + cash_gain)

            pos.quantity = float(Decimal(str(pos.quantity)) - Decimal(str(quantity)))
            pos.available_quantity = float(
                Decimal(str(pos.available_quantity)) - Decimal(str(quantity))
            )

    else:
        raise ValueError(
            f"Invalid side: {side}. Must be LONG/SHORT (open) or BUY/SELL (close)"
        )

    # Create trade record
    trade = dict(
        order_id=order.id,
        account_id=account.id,
        symbol=symbol,
        name=name,
        market="CRYPTO",
        side=order.side,
        price=float(exec_price),
        quantity=quantity,
        commission=float(taker_fee),
        taker_fee=float(taker_fee),
        interest_charged=float(interest_charged),
        trade_time=now,
    )

    # Mark order as filled
    order.filled_quantity = quantity
    order.status = "FILLED"

    return _result(account, pos, order, trade)


def plan_spot(
    account_values,
    position_values,
    order_values,
    *,
    execution_price: Decimal,
    now: datetime,
) -> LedgerPlan | None:
    """Plan a spot/US fill; None retains an unaffordable pending order."""
    account, position, order = (
        _copy(account_values),
        _copy(position_values),
        _copy(order_values),
    )
    quantity = Decimal(str(order.quantity))
    notional = execution_price * quantity
    commission = commission_for(notional)
    leverage = Decimal(str(order.leverage))
    # Re-check funds and positions (prevent concurrency issues)
    if order.side == "BUY":
        if leverage > 1:
            initial_margin = notional / leverage
            cash_needed = initial_margin + commission
        else:
            cash_needed = notional + commission

        if Decimal(str(account.current_cash)) < cash_needed:
            return None

        # Update position

        if order.market == "US" and position and position.side == "SHORT":
            # Cover short position for US stocks
            if Decimal(str(position.quantity)) < quantity:
                return None

            # Deduct cash for buyback
            account.current_cash = float(
                Decimal(str(account.current_cash)) - cash_needed
            )

            position.quantity = float(Decimal(str(position.quantity)) - quantity)
            position.available_quantity = float(
                Decimal(str(position.available_quantity)) - quantity
            )
            if position.quantity <= 0:
                position.side = None
            # Keep avg_cost for remaining short position
        else:
            # Deduct cash
            account.current_cash = float(
                Decimal(str(account.current_cash)) - cash_needed
            )

            if not position:
                position = _position(
                    version="v1",
                    account_id=account.id,
                    symbol=order.symbol,
                    name=order.name,
                    market=order.market,
                    quantity=0,
                    available_quantity=0,
                    avg_cost=0,
                    leverage=1,
                )

            # Calculate new average cost and leverage (use Decimal for precision)
            old_qty = Decimal(str(position.quantity))
            old_cost = Decimal(str(position.avg_cost))
            old_leverage = Decimal(str(position.leverage))

            new_qty = old_qty + quantity

            if old_qty == 0:
                new_avg_cost = execution_price
                new_leverage = leverage
            else:
                old_notional = old_cost * old_qty
                new_notional = notional + old_notional
                new_avg_cost = new_notional / new_qty
                # Update leverage (weighted average)
                new_leverage = (
                    old_notional * old_leverage + notional * leverage
                ) / new_notional

            position.side = "LONG"
            position.quantity = float(new_qty)
            position.available_quantity = float(
                Decimal(str(position.available_quantity)) + quantity
            )
            position.avg_cost = float(new_avg_cost)
            position.leverage = int(new_leverage)

    else:  # SELL
        # Check position

        if order.market == "US" and (position is None or position.side == "SHORT"):
            # Open or increase US stock short position
            if not position:
                position = _position(
                    version="v1",
                    account_id=account.id,
                    symbol=order.symbol,
                    name=order.name,
                    market=order.market,
                    quantity=0,
                    available_quantity=0,
                    avg_cost=0,
                    leverage=1,
                    side="SHORT",
                )

            old_qty = Decimal(str(position.quantity))
            old_cost = Decimal(str(position.avg_cost))
            new_qty = old_qty + quantity
            if old_qty == 0:
                new_avg_cost = execution_price
            else:
                old_notional = old_cost * old_qty
                new_notional = notional + old_notional
                new_avg_cost = new_notional / new_qty

            position.quantity = float(new_qty)
            position.available_quantity = float(new_qty)
            position.avg_cost = float(new_avg_cost)
            position.side = "SHORT"

            cash_gain = notional - commission
            account.current_cash = float(Decimal(str(account.current_cash)) + cash_gain)
        else:
            if not position or Decimal(str(position.available_quantity)) < quantity:
                return None

            # Reduce position (use Decimal for precision)
            position.quantity = float(Decimal(str(position.quantity)) - quantity)
            position.available_quantity = float(
                Decimal(str(position.available_quantity)) - quantity
            )

            # PnL and cash gain calculation for leveraged positions
            sell_notional = notional
            commission = commission_for(sell_notional)
            position_leverage = Decimal(str(position.leverage))

            if position_leverage > 1:
                # 杠杆仓位卖出，需要计算 PnL
                entry_price = Decimal(str(position.avg_cost))
                pnl = (execution_price - entry_price) * quantity

                # 释放的保证金
                initial_margin_part = (entry_price * quantity) / position_leverage

                cash_gain = initial_margin_part + pnl - commission
            else:
                # 现货卖出
                cash_gain = sell_notional - commission

            account.current_cash = float(Decimal(str(account.current_cash)) + cash_gain)

    # Create trade record
    trade = dict(
        order_id=order.id,
        account_id=account.id,
        symbol=order.symbol,
        name=order.name,
        market=order.market,
        side=order.side,
        price=float(execution_price),
        quantity=float(quantity),
        commission=float(commission),
        trade_time=now,
    )

    # Release frozen (BUY)
    release_frozen(account, order, execution_price, commission)

    # Update order status
    order.filled_quantity = float(quantity)
    order.status = "FILLED"

    return _result(account, position, order, trade)


def plan_cancel(
    account_values, order_values, *, strict: bool = False
) -> LedgerPlan | None:
    """Plan cancellation and frozen-funds release without touching persisted rows."""
    account, order = _copy(account_values), _copy(order_values)
    if order.status != "PENDING":
        return None
    if account is None and strict:
        raise ValueError(
            f"Account {order.account_id} for order {order.order_no} does not exist"
        )
    if account is not None and order.side == "BUY":
        ref_price = float(order.price or 0.0)
        if ref_price <= 0:
            if strict:
                raise ValueError(
                    f"Cannot cancel BUY order {order.order_no} without a reference price"
                )
            ref_price = 100.0
        notional = Decimal(str(ref_price)) * Decimal(order.quantity)
        amount = notional + commission_for(notional)
        account.frozen_cash = float(
            max(Decimal(str(account.frozen_cash)) - amount, Decimal("0"))
        )
    order.status = "CANCELLED"
    return LedgerPlan(vars(account) if account else {}, None, vars(order), None)


def plan_frozen_release(
    account_values, order_values, execution_price, commission
) -> LedgerPlan:
    """Additional manual-order release after the crypto fill plan."""
    account, order = _copy(account_values), _copy(order_values)
    release_frozen(account, order, execution_price, commission)
    return LedgerPlan(vars(account), None, vars(order), None)


def plan_open_size(
    account_values: Mapping[str, Any],
    price: float,
    market: str,
    size_mode: str,
    target_portion_of_balance: Optional[float],
    usd_amount: Optional[float],
) -> tuple[float, float]:
    account = _copy(account_values)
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
        portion = float(
            target_portion_of_balance if target_portion_of_balance is not None else 0.0
        )
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


def plan_close_size(
    position_values: Mapping[str, Any],
    market: str,
    price: float,
    size_mode: str,
    target_portion_of_balance: Optional[float],
    usd_amount: Optional[float],
    close_ratio: Optional[float],
) -> tuple[float, float]:
    position = _copy(position_values)
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


def plan_create_order(
    account_values,
    position_values,
    *,
    symbol: str,
    name: str,
    market: str,
    side: str,
    order_type: str,
    price: float | None,
    quantity: float,
    leverage: int,
    check_price: Decimal,
    order_no: str,
    now: datetime,
) -> Mapping[str, Any]:
    """Validate a pending order against detached balances and positions."""
    account, position = _copy(account_values), _copy(position_values)
    active = position is not None and position.quantity > 0
    if market == "CRYPTO" and active:
        if position.side == "SHORT":
            raise ValueError(
                "Manual crypto orders cannot modify an existing short position"
            )
        if side == "BUY" and (position.leverage or 1) != leverage:
            raise ValueError("Cannot add to position with different leverage")
    if quantity <= 0:
        raise ValueError("Order quantity must be > 0")
    if order_type == "LIMIT" and (price is None or price <= 0):
        raise ValueError("Limit order must specify valid order price")
    if not check_price.is_finite() or check_price <= 0:
        raise ValueError("Market or limit price must be positive and finite")
    if side == "BUY":
        notional = check_price * Decimal(str(quantity))
        commission = commission_for(notional)
        cash_needed = (
            notional / Decimal(str(leverage)) if leverage > 1 else notional
        ) + commission
        if Decimal(str(account.current_cash)) < cash_needed:
            raise ValueError(
                f"Insufficient cash. Need ${cash_needed:.2f}, current cash ${account.current_cash:.2f}"
            )
    elif not (market == "US" and (position is None or position.side == "SHORT")):
        if not position or Decimal(str(position.available_quantity)) < Decimal(
            str(quantity)
        ):
            available_qty = float(position.available_quantity) if position else 0
            raise ValueError(
                f"Insufficient positions. Need {quantity} {symbol}, available {available_qty} {symbol}"
            )
    return dict(
        version="v1",
        account_id=account.id,
        order_no=order_no,
        symbol=symbol,
        name=name,
        market=market,
        side=side,
        order_type=order_type,
        price=price,
        quantity=quantity,
        leverage=leverage,
        filled_quantity=0,
        status="PENDING",
        order_time=now,
    )
