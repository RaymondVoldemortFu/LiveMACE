"""
Order matching service
Implements conditional execution logic for limit orders
"""

import uuid
from decimal import Decimal
from typing import Optional, Tuple
from sqlalchemy.orm import Session
import logging

from database.models import (
    Account,
    CRYPTO_COMMISSION_RATE,
    CRYPTO_MIN_COMMISSION,
    Order,
    Position,
    Trade,
)
from .market_data import get_trading_price as get_last_price
from services.time_source import now_utc

logger = logging.getLogger(__name__)


from benchmark.application.trading.planner import commission_for as _calc_commission


def _guard_manual_crypto_position(db, account_id, symbol, market):
    if market != "CRYPTO":
        return
    position = db.query(Position).filter(
        Position.account_id == account_id, Position.symbol == symbol,
        Position.market == market, Position.side == "SHORT", Position.quantity > 0,
    ).first()
    if position is not None:
        raise ValueError("Manual crypto orders cannot modify an existing short position")


def create_order(db: Session, account: Account, symbol: str, name: str,
                side: str, order_type: str, price: Optional[float], quantity: float, leverage: int = 1, market: str = "CRYPTO") -> Order:
    """
    Create limit order

    Args:
        db: Database session
        account: Account object
        symbol: crypto Symbol
        name: crypto name
        side: Buy/side direction (BUY/SELL)
        order_type: Order type (MARKET/LIMIT)
        price: Order price (required for limit orders)
        quantity: Order quantity

    Returns:
        Created order object

    Raises:
        ValueError: Parameter validation failed or insufficient funds/positions
    """
    from benchmark.application.trading.planner import plan_create_order
    from benchmark.persistence.ledger import SqlAlchemyLedgerRepository
    ledger = SqlAlchemyLedgerRepository(db)
    if order_type == "MARKET":
        try:
            check_price = Decimal(str(get_last_price(symbol, market)))
        except Exception as exc:
            raise ValueError("Unable to get market price for market order") from exc
    else:
        check_price = Decimal(str(price or 0))
    plan = plan_create_order(ledger.account_values(account), ledger.position_values(account.id, symbol, market),
                             symbol=symbol, name=name, market=market, side=side,
                             order_type=order_type, price=price, quantity=quantity, leverage=leverage,
                             check_price=check_price, order_no=uuid.uuid4().hex[:16], now=now_utc())
    return ledger.create_order(plan)


def check_and_execute_order(
    db: Session,
    order: Order,
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
) -> bool:
    """
    Check and execute limit order

    Execution conditions:
    - Buy: order price >= current market price and sufficient funds
    - Sell: order price <= current market price and sufficient positions

    Args:
        db: Database session
        order: Order to check

    Returns:
        Whether order was executed
    """
    if order.status != "PENDING":
        return False
    
    # A direction can change after a LIMIT order was created. Keep this order
    # pending while the incompatible position exists; do not abort other orders.
    try:
        _guard_manual_crypto_position(db, order.account_id, order.symbol, order.market)
    except ValueError:
        return False
    try:
        # Get current market price
        current_price = get_last_price(order.symbol, order.market)
        current_price_decimal = Decimal(str(current_price))
        if not current_price_decimal.is_finite() or current_price_decimal <= 0:
            raise ValueError("Market price must be positive and finite")

        # Get user information
        account = db.query(Account).filter(Account.id == order.account_id).first()
        if not account:
            if raise_on_error:
                raise ValueError(
                    f"Account {order.account_id} for order {order.order_no} does not exist"
                )
            logger.error(f"Account corresponding to order {order.order_no} does not exist")
            return False

        # Check execution conditions
        should_execute = False
        execution_price = current_price_decimal

        if order.order_type == "MARKET":
            # Market order executes immediately
            should_execute = True
            execution_price = current_price_decimal

        elif order.order_type == "LIMIT":
            # Limit order conditional execution
            limit_price = Decimal(str(order.price))

            if order.side == "BUY":
                # Buy: order price >= current market price
                if limit_price >= current_price_decimal:
                    should_execute = True
                    execution_price = current_price_decimal  # Execute at market price

            else:  # SELL
                # Sell: order price <= current market price
                if limit_price <= current_price_decimal:
                    should_execute = True
                    execution_price = current_price_decimal  # Execute at market price

        elif raise_on_error:
            raise ValueError(f"Unsupported order type: {order.order_type}")

        if not should_execute:
            logger.debug(f"Order {order.order_no} does not meet execution condition: {order.side} {order.price} vs market {current_price}")
            return False

        # Execute order
        return _execute_order(
            db,
            order,
            account,
            execution_price,
            manage_transaction=manage_transaction,
            raise_on_error=raise_on_error,
        )

    except Exception as e:
        if raise_on_error:
            raise
        logger.error(f"Error checking order {order.order_no}: {e}")
        return False


def _execute_order(
    db: Session,
    order: Order,
    account: Account,
    execution_price: Decimal,
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
) -> bool:
    """
    Execute order fill

    Args:
        db: Database session
        order: Order object
        account: Account object
        execution_price: Execution price

    Returns:
        Whether execution was successful
    """
    try:
        quantity = Decimal(str(order.quantity))
        notional = execution_price * quantity
        commission = _calc_commission(notional)
        leverage = Decimal(str(order.leverage))

        if order.market == "CRYPTO":
            position = db.query(Position).filter(
                Position.account_id == account.id, Position.symbol == order.symbol,
                Position.market == order.market).first()
            active = position is not None and position.quantity > 0
            if leverage > 1 or (active and (position.leverage or 1) > 1):
                if order.side == "BUY" and active and position.leverage != int(leverage):
                    return False
                from services.order_executor_leverage import place_and_execute_crypto
                with db.begin_nested() as settlement:
                    try:
                        place_and_execute_crypto(
                            db, account.id, order.symbol, order.name,
                            "LONG" if order.side == "BUY" else "SELL", order.order_type,
                            order.price, float(quantity),
                            leverage=int(leverage) if order.side == "BUY" else int(position.leverage if active else 1),
                            manage_transaction=False, existing_order=order, execution_price=execution_price,
                        )
                    except ValueError:
                        # A previously valid pending order can become unaffordable
                        # or oversized. Roll back only this settlement and continue.
                        settlement.rollback()
                        return False
                from benchmark.application.trading.planner import plan_frozen_release
                from benchmark.persistence.ledger import SqlAlchemyLedgerRepository
                ledger = SqlAlchemyLedgerRepository(db)
                release = plan_frozen_release(ledger.account_values(account), ledger.order_values(order), execution_price, commission)
                ledger.apply(release, account, order)
                if manage_transaction:
                    db.commit()
                return True

        from benchmark.application.trading.planner import plan_spot
        from benchmark.persistence.ledger import SqlAlchemyLedgerRepository

        ledger = SqlAlchemyLedgerRepository(db)
        plan = plan_spot(*ledger.inputs(account, order), execution_price=execution_price, now=now_utc())
        if plan is None:
            return False
        ledger.apply(plan, account, order)

        if manage_transaction:
            db.commit()
        else:
            db.flush()
        
        logger.info(f"Order {order.order_no} executed: {order.side} {quantity} {order.symbol} @ ${execution_price}")
        return True
        
    except Exception as e:
        if manage_transaction:
            db.rollback()
        if raise_on_error:
            raise
        logger.error(f"Error executing order {order.order_no}: {e}")
        return False


def get_pending_orders(db: Session, account_id: Optional[int] = None) -> list[Order]:
    """
    Get pending orders

    Args:
        db: Database session
        account_id: Account ID, when None get all accounts' pending orders

    Returns:
        List of pending orders
    """
    query = db.query(Order).filter(Order.status == "PENDING")
    
    if account_id is not None:
        query = query.filter(Order.account_id == account_id)
    
    return query.order_by(Order.created_at).all()


def cancel_order(
    db: Session,
    order: Order,
    reason: str = "User cancelled",
    *,
    manage_transaction: bool = True,
    raise_on_error: bool = False,
) -> bool:
    """
    Cancel order

    Args:
        db: Database session
        order: Order object
        reason: Cancel reason

    Returns:
        Whether cancellation was successful
    """
    if order.status != "PENDING":
        return False
    
    try:
        from benchmark.application.trading.planner import plan_cancel
        from benchmark.persistence.ledger import SqlAlchemyLedgerRepository
        account = db.query(Account).filter(Account.id == order.account_id).first()
        ledger = SqlAlchemyLedgerRepository(db)
        plan = plan_cancel(ledger.account_values(account), ledger.order_values(order), strict=raise_on_error)
        if plan is None:
            return False
        ledger.apply(plan, account, order)
        if manage_transaction:
            db.commit()
        else:
            db.flush()
        
        logger.info(f"Order {order.order_no} cancelled: {reason}")
        return True
        
    except Exception as e:
        if manage_transaction:
            db.rollback()
        if raise_on_error:
            raise
        logger.error(f"Error cancelling order {order.order_no}: {e}")
        return False


def process_all_pending_orders(db: Session) -> Tuple[int, int]:
    """
    Process all pending orders

    Args:
        db: Database session

    Returns:
        (Executed orders count, Total checked orders)
    """
    pending_orders = get_pending_orders(db)
    executed_count = 0
    checked_count = 0
    from services.scheduler import shutdown_cancellation_requested

    for order in pending_orders:
        if shutdown_cancellation_requested():
            break
        checked_count += 1
        if check_and_execute_order(db, order):
            executed_count += 1

    logger.info(f"Processing pending orders: checked {checked_count} orders, executed {executed_count} orders")
    return executed_count, checked_count
