from decimal import Decimal
from sqlalchemy.orm import Session
from database.models import Position
from .market_data import get_last_price


def calc_positions_market_value(db: Session, account_id: int) -> float:
    """
    Calculate total equity in positions (for leveraged positions: margin + unrealized P&L).

    For leveraged positions, the equity is NOT the full market value (quantity * price),
    but rather the entry margin plus unrealized profit/loss.

    Equity = Entry Margin + Unrealized P&L
                     = (entry_notional / leverage) + side_aware_unrealized_pnl
                     = (avg_cost * quantity / leverage) + pnl

        where:
            - LONG pnl  = quantity * (current_price - avg_cost)
            - SHORT pnl = quantity * (avg_cost - current_price)

    NOTE:
    Do NOT use current market value / leverage as margin here. Doing so adds an
    extra term proportional to price change, which effectively re-introduces
    leverage into PnL during settlement/checkpoint equity calculation.

    Args:
        db: Database session
        account_id: Account ID

    Returns:
        Total equity in positions, returns 0 if price cannot be obtained
    """
    positions = db.query(Position).filter(Position.account_id == account_id).all()
    total = Decimal("0")

    for p in positions:
        try:
            price = Decimal(str(get_last_price(p.symbol, p.market)))
            position_equity = calculate_position_market_value(p, price)
            total += position_equity
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Cannot get price for {p.symbol}.{p.market}, skipping position value calculation: {e}")
            continue

    return float(total)


def calc_positions_value(db: Session, account_id: int) -> float:
    """
    计算所有仓位的名义总价值 (sum(quantity * price * leverage))。

    WARNING: This returns NOTIONAL value (exposure), not equity!
    For calculating account assets/profit, use calc_positions_market_value() instead.

    Args:
        db: Database session
        account_id: Account ID

    Returns:
        Total notional value of positions, returns 0 if price cannot be obtained
    """
    positions = db.query(Position).filter(Position.account_id == account_id).all()
    total = Decimal("0")

    for p in positions:
        try:
            price = Decimal(str(get_last_price(p.symbol, p.market)))
            total += price * Decimal(str(p.quantity)) * Decimal(str(p.leverage))
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Cannot get price for {p.symbol}.{p.market}, skipping position value calculation: {e}")
            continue

    return float(total)


def calculate_position_market_value(p, price) -> Decimal:
    """Position equity using a supplied price, without database or network I/O."""
    price = Decimal(str(price))
    quantity = Decimal(str(p.quantity))
    avg_cost = Decimal(str(p.avg_cost))
    leverage = Decimal(str(p.leverage)) if p.leverage and p.leverage > 0 else Decimal("1")

    # Market value of position
    market_value = quantity * price

    # For leveraged positions, count entry margin + unrealized P&L
    # (entry margin must be based on avg_cost, not current price).
    if leverage > 1:
        # Entry initial margin used
        entry_margin = (quantity * avg_cost) / leverage
        # Unrealized P&L (direction-aware)
        side = getattr(p, 'side', None) or "LONG"
        if side.upper() == "SHORT":
            unrealized_pnl = quantity * (avg_cost - price)
        else:
            unrealized_pnl = quantity * (price - avg_cost)
        # Position equity = margin + P&L
        position_equity = entry_margin + unrealized_pnl
    else:
        # Non-leveraged position:
        # - LONG  equity = +quantity * price
        # - SHORT equity = -quantity * price
        # (short spot inventory is a liability and must be signed)
        side = getattr(p, 'side', None) or "LONG"
        signed_quantity = -quantity if side.upper() == "SHORT" else quantity
        position_equity = signed_quantity * price
    return position_equity
