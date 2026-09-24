import uuid
import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from database.models import (
    Order, Position, Trade, Account,
    CRYPTO_TAKER_FEE_RATE, CRYPTO_INTEREST_RATE_HOURLY, CRYPTO_MAX_LEVERAGE,
    CRYPTO_MIN_ORDER_QUANTITY,
)
from .market_data import get_trading_price as get_last_price
from services.time_source import now_utc


from benchmark.application.trading.planner import crypto_fee as _calc_crypto_fee, position_interest


def _calculate_position_interest(position) -> Decimal:
    return position_interest(position, now_utc())


def place_and_execute_crypto(
    db: Session,
    account_id: int,
    symbol: str,
    name: str,
    side: str,
    order_type: str,
    price: float | None,
    quantity: float,
    leverage: int = 1,
    *,
    manage_transaction: bool = True,
    existing_order: Order | None = None,
    execution_price: Decimal | None = None,
) -> Order:
    """
    Place and execute a CRYPTO order with leverage support.
    
    Args:
        account_id: Trading account ID
        symbol: Trading pair (e.g., 'BTC/USDT')
        side: 'LONG' (open long) / 'SHORT' (open short) / 'BUY' (close short) / 'SELL' (close long)
        leverage: Leverage multiplier (1 = spot, 2-50 = leveraged)
        quantity: Amount in base currency (e.g., BTC amount for BTC/USDT)
    
    Returns:
        Executed Order
    """
    # Fetch account from database using account_id
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise ValueError(f"Account with id {account_id} not found")
    
    if leverage < 1 or leverage > CRYPTO_MAX_LEVERAGE:
        raise ValueError(f"Leverage must be between 1 and {CRYPTO_MAX_LEVERAGE}")
    
    # Validate quantity
    if quantity < CRYPTO_MIN_ORDER_QUANTITY:
        raise ValueError(f"Quantity must be >= {CRYPTO_MIN_ORDER_QUANTITY}")
    
    # Get execution price
    exec_price = (execution_price if execution_price is not None else
                  Decimal(str(price if (order_type == "LIMIT" and price) else get_last_price(symbol, "CRYPTO"))))
    notional = exec_price * Decimal(str(quantity))
    
    # Calculate fees
    taker_fee = _calc_crypto_fee(notional, leverage)
    
    if existing_order is not None:
        order = existing_order
    else:
        from benchmark.persistence.ledger import SqlAlchemyLedgerRepository
        order = SqlAlchemyLedgerRepository(db).create_order(dict(
            version="v1",
            account_id=account.id,
            order_no=uuid.uuid4().hex[:16],
            symbol=symbol,
            name=name,
            market="CRYPTO",
            side=side.upper(),
            order_type=order_type,
            price=float(exec_price),
            quantity=quantity,
            leverage=leverage,
            filled_quantity=0,
            status="PENDING",
            order_time=now_utc(),
        ))

    from benchmark.application.trading.planner import plan_crypto
    from benchmark.persistence.ledger import SqlAlchemyLedgerRepository

    ledger = SqlAlchemyLedgerRepository(db)
    plan = plan_crypto(*ledger.inputs(account, order), side=side, quantity=quantity,
                       leverage=leverage, exec_price=exec_price, now=now_utc())
    ledger.apply(plan, account, order)

    if manage_transaction:
        db.commit()
        db.refresh(order)
        db.refresh(account)
    else:
        db.flush()
    
    return order
