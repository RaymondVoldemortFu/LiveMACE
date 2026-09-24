"""
Snapshot Service - Creates and manages account snapshots for evaluation
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session

from database.models import Account, AccountSnapshot, Position
from services.asset_calculator import calc_positions_market_value

logger = logging.getLogger(__name__)


def create_account_snapshot(
    db: Session, 
    account_id: int, 
    timestamp: Optional[datetime] = None,
    *, prices=None
) -> Optional[AccountSnapshot]:
    """
    Create a snapshot of the current account state
    
    Args:
        db: Database session
        account_id: Account ID to snapshot
        timestamp: Optional timestamp (defaults to current UTC time)
    
    Returns:
        Created snapshot or None if failed
    """
    try:
        # Get account
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            logger.error(f"Account {account_id} not found")
            return None
        
        # Use provided timestamp or current UTC time
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Calculate positions value
        if prices is None:
            positions_value = calc_positions_market_value(db, account_id)
        else:
            from services.asset_calculator import calculate_position_market_value
            positions = db.query(Position).filter(Position.account_id == account_id, Position.quantity != 0).all()
            positions_value = sum((calculate_position_market_value(p, prices[p.symbol]) for p in positions), Decimal('0'))
        
        # Calculate total equity
        cash = float(account.current_cash)
        total_equity = Decimal(str(cash)) + Decimal(str(positions_value))
        
        # Create snapshot
        snapshot = AccountSnapshot(
            account_id=account_id,
            ts=timestamp,
            total_equity=Decimal(str(total_equity)),
            cash=Decimal(str(cash)),
            positions_value=Decimal(str(positions_value))
        )
        
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        
        logger.info(f"Created snapshot for account {account_id}: equity=${total_equity:.2f}, cash=${cash:.2f}, positions=${positions_value:.2f}")
        return snapshot
        
    except Exception as e:
        logger.error(f"Failed to create snapshot for account {account_id}: {e}")
        db.rollback()
        return None


def create_snapshots_for_all_accounts(db: Session) -> int:
    """
    Create snapshots for all active accounts
    
    Args:
        db: Database session
    
    Returns:
        Number of snapshots created
    """
    try:
        # Get all active accounts
        accounts = db.query(Account).filter(Account.is_active == "true").all()
        
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        created_count = 0
        
        for account in accounts:
            snapshot = create_account_snapshot(db, account.id, timestamp)
            if snapshot:
                created_count += 1
        
        logger.info(f"Created {created_count} snapshots for {len(accounts)} active accounts")
        return created_count
        
    except Exception as e:
        logger.error(f"Failed to create snapshots for all accounts: {e}")
        return 0


def get_snapshots_in_range(
    db: Session,
    account_id: int,
    start_time: datetime,
    end_time: datetime
) -> list:
    """
    Get account snapshots within a time range
    
    Args:
        db: Database session
        account_id: Account ID
        start_time: Start of range
        end_time: End of range
    
    Returns:
        List of snapshots
    """
    try:
        snapshots = db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id,
            AccountSnapshot.ts >= start_time,
            AccountSnapshot.ts <= end_time
        ).order_by(AccountSnapshot.ts).all()
        
        return snapshots
        
    except Exception as e:
        logger.error(f"Failed to get snapshots for account {account_id}: {e}")
        return []


def get_latest_snapshot(db: Session, account_id: int) -> Optional[AccountSnapshot]:
    """
    Get the most recent snapshot for an account
    
    Args:
        db: Database session
        account_id: Account ID
    
    Returns:
        Latest snapshot or None
    """
    try:
        snapshot = db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id
        ).order_by(AccountSnapshot.ts.desc()).first()
        
        return snapshot
        
    except Exception as e:
        logger.error(f"Failed to get latest snapshot for account {account_id}: {e}")
        return None
