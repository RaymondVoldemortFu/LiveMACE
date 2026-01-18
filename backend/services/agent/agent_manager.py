import uuid
import logging
from typing import Optional
from sqlalchemy.orm import Session

from backend.database.models import Account, Position, Order, Trade

logger = logging.getLogger(__name__)

def fork_agent(
    db: Session,
    original_account_id: int,
    new_agent_type: str,
    new_model: Optional[str] = None
) -> Account:
    """
    Fork an existing agent (Account) into a new one.
    Copies:
    - Account configuration (with new name and agent_type)
    - Positions
    - Orders (history)
    - Trades (history)
    
    The new agent starts with the same state as the original.
    
    Args:
        db: Database session
        original_account_id: ID of the account to fork
        new_agent_type: The agent_type for the new account
        new_model: Optional model override. If None, uses original's model.
        
    Returns:
        The newly created Account object.
    """
    # 1. Fetch original account
    original_account = db.query(Account).filter(Account.id == original_account_id).first()
    if not original_account:
        raise ValueError(f"Account with ID {original_account_id} not found.")

    # 2. Create new Account
    new_name = f"{original_account.name}_fork_{new_agent_type}"
    
    # Ensure name uniqueness (simple check, append suffix if exists)
    existing = db.query(Account).filter(Account.name == new_name).first()
    if existing:
        new_name = f"{new_name}_{uuid.uuid4().hex[:6]}"

    new_account = Account(
        user_id=original_account.user_id,
        version=original_account.version,
        name=new_name,
        account_type=original_account.account_type, # AI or MANUAL
        agent_type=new_agent_type,
        is_active="true", # Active by default
        model=new_model if new_model else original_account.model,
        base_url=original_account.base_url,
        api_key=original_account.api_key,
        initial_capital=original_account.initial_capital,
        current_cash=original_account.current_cash,
        frozen_cash=original_account.frozen_cash,
        margin_used=original_account.margin_used,
        maintenance_margin_ratio=original_account.maintenance_margin_ratio,
        # Timestamps will be auto-generated
    )
    db.add(new_account)
    db.flush() # Get ID
    
    logger.info(f"Forking agent {original_account.name} (ID: {original_account.id}) to {new_name} (ID: {new_account.id})")

    # 3. Clone Positions
    original_positions = db.query(Position).filter(Position.account_id == original_account_id).all()
    for pos in original_positions:
        new_pos = Position(
            version=pos.version,
            account_id=new_account.id,
            symbol=pos.symbol,
            name=pos.name,
            market=pos.market,
            quantity=pos.quantity,
            available_quantity=pos.available_quantity,
            avg_cost=pos.avg_cost,
            leverage=pos.leverage,
            side=pos.side,
            accumulated_interest=pos.accumulated_interest,
            last_interest_time=pos.last_interest_time,
            # update_time, created_at handled by DB defaults
        )
        db.add(new_pos)
    
    # 4. Clone Orders and Trades
    # We need to map old order IDs to new Order objects to link Trades correctly
    original_orders = db.query(Order).filter(Order.account_id == original_account_id).all()
    
    # Map old_order_id -> New Order Object
    order_map = {}
    
    for order in original_orders:
        new_order = Order(
            version=order.version,
            account_id=new_account.id,
            # Generate new unique order_no
            order_no=uuid.uuid4().hex[:16], 
            symbol=order.symbol,
            name=order.name,
            market=order.market,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            quantity=order.quantity,
            leverage=order.leverage,
            filled_quantity=order.filled_quantity,
            status=order.status,
            order_time=order.order_time, # Keep original time for history? Yes.
            # created_at/updated_at will be new
        )
        db.add(new_order)
        order_map[order.id] = new_order
    
    # We flush orders to ensure they are tracked by session, though not strictly needed for relationship assignment if using object refs
    # But let's be safe
    
    # 5. Clone Trades
    original_trades = db.query(Trade).filter(Trade.account_id == original_account_id).all()
    
    for trade in original_trades:
        if trade.order_id not in order_map:
            logger.warning(f"Trade {trade.id} refers to order {trade.order_id} which was not found in account orders. Skipping.")
            continue
            
        new_trade = Trade(
            account_id=new_account.id,
            # Link to new order
            order=order_map[trade.order_id], 
            symbol=trade.symbol,
            name=trade.name,
            market=trade.market,
            side=trade.side,
            price=trade.price,
            quantity=trade.quantity,
            commission=trade.commission,
            taker_fee=trade.taker_fee,
            interest_charged=trade.interest_charged,
            trade_time=trade.trade_time, # Keep original time
        )
        db.add(new_trade)

    # 6. Commit all changes
    db.commit()
    db.refresh(new_account)
    
    return new_account

def kill_agent(db: Session, account_id: int):
    """
    Stop an agent by setting it to inactive.
    Does not delete data.
    
    Args:
        db: Database session
        account_id: ID of the account to stop
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        logger.warning(f"Attempted to kill non-existent agent {account_id}")
        return

    if account.is_active == "false":
        logger.info(f"Agent {account_id} is already inactive.")
        return

    account.is_active = "false"
    db.commit()
    logger.info(f"Agent {account.name} (ID: {account_id}) has been stopped (set to inactive).")

