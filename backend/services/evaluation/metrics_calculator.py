"""
Metrics Calculator Service
Calculates financial metrics from account snapshots for rule evaluation
"""

import logging
from typing import List, Dict
from datetime import datetime
from decimal import Decimal
import math
from sqlalchemy.orm import Session

from database.models import AccountSnapshot, Position, AssetMetadata, Trade

# Import centralized crypto sector classification
try:
    from config.asset_config import CRYPTO_SECTOR_MAP
except ImportError:
    # Fallback for absolute import
    from backend.config.asset_config import CRYPTO_SECTOR_MAP

logger = logging.getLogger(__name__)


class MetricsCalculator:
    """Calculates financial metrics from account data"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def calculate_volatility(self, snapshots: List[AccountSnapshot]) -> float:
        """
        Calculate portfolio volatility (annualized)
        
        Args:
            snapshots: List of account snapshots in chronological order
            
        Returns:
            Annualized volatility (e.g., 0.15 for 15%)
        """
        if len(snapshots) < 2:
            return 0.0
        
        # Calculate returns
        returns = []
        for i in range(1, len(snapshots)):
            prev_equity = float(snapshots[i-1].total_equity)
            curr_equity = float(snapshots[i].total_equity)
            
            if prev_equity > 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)
        
        if not returns:
            return 0.0
        
        # Calculate standard deviation of returns
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        # Annualize (assuming hourly snapshots, 24*365 periods per year)
        periods_per_year = 24 * 365
        annualized_volatility = std_dev * math.sqrt(periods_per_year)
        
        return annualized_volatility
    
    def calculate_turnover(self, account_id: int, snapshots: List[AccountSnapshot]) -> float:
        """
        Calculate portfolio turnover rate
        
        Args:
            account_id: Account ID
            snapshots: List of account snapshots in chronological order
            
        Returns:
            Turnover rate (e.g., 2.0 for 200%)
        """
        if len(snapshots) < 2:
            return 0.0
        
        start_time = snapshots[0].ts
        end_time = snapshots[-1].ts
        avg_equity = sum(float(s.total_equity) for s in snapshots) / len(snapshots)
        
        if avg_equity == 0:
            return 0.0
        
        # Get all trades in the period
        trades = self.db.query(Trade).filter(
            Trade.account_id == account_id,
            Trade.trade_time >= start_time,
            Trade.trade_time <= end_time
        ).all()
        
        # Calculate total trading volume
        total_volume = sum(abs(float(t.quantity) * float(t.price)) for t in trades)
        
        # Turnover = Total Volume / Average Equity
        turnover = total_volume / avg_equity if avg_equity > 0 else 0
        
        return turnover
    
    def calculate_sector_allocation(self, account_id: int) -> Dict[str, float]:
        """
        Calculate current portfolio allocation by sector
        
        Args:
            account_id: Account ID
            
        Returns:
            Dict mapping sector name to allocation ratio
        """
        # Get current positions
        positions = self.db.query(Position).filter(
            Position.account_id == account_id,
            Position.quantity > 0
        ).all()
        
        if not positions:
            return {}
        
        # Calculate total portfolio value
        from services.market_data import get_last_price
        
        total_value = 0
        sector_values = {}
        
        for pos in positions:
            try:
                current_price = get_last_price(pos.symbol, pos.market)
                if not current_price:
                    continue
                
                position_value = float(pos.quantity) * current_price
                total_value += position_value
                
                # Use built-in sector mapping instead of asset_metadata table
                sector = CRYPTO_SECTOR_MAP.get(pos.symbol, "Unknown")
                sector_values[sector] = sector_values.get(sector, 0) + position_value
                
            except Exception as e:
                logger.error(f"Error calculating value for {pos.symbol}: {e}")
                continue
        
        if total_value == 0:
            return {}
        
        # Convert to ratios
        sector_allocation = {
            sector: value / total_value
            for sector, value in sector_values.items()
        }
        
        return sector_allocation
    
    def calculate_avg_transaction_cost(
        self, 
        account_id: int, 
        start_time: datetime, 
        end_time: datetime
    ) -> float:
        """
        Calculate average transaction cost ratio
        
        Args:
            account_id: Account ID
            start_time: Period start
            end_time: Period end
            
        Returns:
            Average transaction cost ratio (e.g., 0.001 for 0.1%)
        """
        trades = self.db.query(Trade).filter(
            Trade.account_id == account_id,
            Trade.trade_time >= start_time,
            Trade.trade_time <= end_time
        ).all()
        
        if not trades:
            return 0.0
        
        # Calculate actual transaction costs from trade records
        total_cost = 0
        total_notional = 0
        
        for trade in trades:
            notional_value = abs(float(trade.quantity) * float(trade.price))
            total_notional += notional_value
            
            # Sum up all fees: commission, taker fee, and interest
            trade_cost = (
                float(trade.commission or 0) + 
                float(trade.taker_fee or 0) + 
                float(trade.interest_charged or 0)
            )
            total_cost += trade_cost
        
        # Calculate average cost ratio
        if total_notional > 0:
            avg_cost_ratio = total_cost / total_notional
        else:
            avg_cost_ratio = 0.0
        
        return avg_cost_ratio
    
    def calculate_sharpe_ratio(self, snapshots: List[AccountSnapshot], risk_free_rate: float = 0.0) -> float:
        """
        Calculate Sharpe ratio
        
        Args:
            snapshots: List of account snapshots in chronological order
            risk_free_rate: Annual risk-free rate (default 0%)
            
        Returns:
            Sharpe ratio
        """
        if len(snapshots) < 2:
            return 0.0
        
        # Calculate returns
        returns = []
        for i in range(1, len(snapshots)):
            prev_equity = float(snapshots[i-1].total_equity)
            curr_equity = float(snapshots[i].total_equity)
            
            if prev_equity > 0:
                ret = (curr_equity - prev_equity) / prev_equity
                returns.append(ret)
        
        if not returns:
            return 0.0
        
        # Calculate mean return and volatility
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return 0.0
        
        # Annualize (assuming hourly snapshots)
        periods_per_year = 24 * 365
        annualized_return = mean_return * periods_per_year
        annualized_volatility = std_dev * math.sqrt(periods_per_year)
        
        # Sharpe = (Return - RiskFreeRate) / Volatility
        sharpe = (annualized_return - risk_free_rate) / annualized_volatility
        
        return sharpe
    
    def calculate_max_drawdown(self, snapshots: List[AccountSnapshot]) -> float:
        """
        Calculate maximum drawdown
        
        Args:
            snapshots: List of account snapshots in chronological order
            
        Returns:
            Maximum drawdown ratio (e.g., 0.05 for 5%)
        """
        if len(snapshots) < 2:
            return 0.0
        
        max_equity = 0
        max_drawdown = 0
        
        for snapshot in snapshots:
            equity = float(snapshot.total_equity)
            
            # Update peak
            if equity > max_equity:
                max_equity = equity
            
            # Calculate drawdown from peak
            if max_equity > 0:
                drawdown = (max_equity - equity) / max_equity
                max_drawdown = max(max_drawdown, drawdown)
        
        return max_drawdown
