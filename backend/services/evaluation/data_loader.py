from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Dict, Optional
from datetime import datetime

from database.models import Account, AIDecisionLog, Trade, AgentTrace, Position
from services.asset_calculator import calc_positions_value

class EvaluationDataLoader:
    """
    Data loader for evaluation purposes.
    Fetches agent performance, decisions, and trades from the database.
    """
    
    def __init__(self, db: Session):
        self.db = db

    def get_agent_accounts(self, agent_type: Optional[str] = None) -> List[Account]:
        """
        Get all AI accounts, optionally filtered by agent architecture.
        """
        query = self.db.query(Account).filter(Account.account_type == "AI")
        if agent_type:
            query = query.filter(Account.agent_type == agent_type)
        return query.all()

    def get_trades(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[Trade]:
        """
        Get trades for a specific account within a time range.
        """
        query = self.db.query(Trade).filter(Trade.account_id == account_id)
        if start_time:
            query = query.filter(Trade.trade_time >= start_time)
        if end_time:
            query = query.filter(Trade.trade_time <= end_time)
        return query.order_by(Trade.trade_time).all()

    def get_decisions(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[AIDecisionLog]:
        """
        Get AI decisions for a specific account within a time range.
        """
        query = self.db.query(AIDecisionLog).filter(AIDecisionLog.account_id == account_id)
        if start_time:
            query = query.filter(AIDecisionLog.decision_time >= start_time)
        if end_time:
            query = query.filter(AIDecisionLog.decision_time <= end_time)
        return query.order_by(AIDecisionLog.decision_time).all()

    def get_traces(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[AgentTrace]:
        """
        Get detailed execution traces for a specific account.
        Note: Traces can be voluminous.
        """
        query = self.db.query(AgentTrace).filter(AgentTrace.account_id == account_id)
        if start_time:
            query = query.filter(AgentTrace.created_at >= start_time)
        if end_time:
            query = query.filter(AgentTrace.created_at <= end_time)
        return query.order_by(AgentTrace.created_at).all()

    def calculate_pnl(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> Dict[str, float]:
        """
        Calculate Profit and Loss (PnL) for an account.
        This is a simplified calculation based on current asset value vs initial capital (or value at start_time).
        
        For a precise period PnL, one would need historical snapshots of account value.
        Here we return current total profit stats.
        """
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return {"total_pnl": 0.0, "roi": 0.0}

        # Calculate current total assets
        positions_val = calc_positions_value(self.db, account_id)
        current_total = float(account.current_cash) + float(positions_val)
        
        initial = float(account.initial_capital)
        
        total_pnl = current_total - initial
        roi = (total_pnl / initial * 100) if initial > 0 else 0.0
        
        return {
            "initial_capital": initial,
            "current_total_assets": current_total,
            "total_pnl": total_pnl,
            "roi": roi
        }

    def get_performance_summary(self, agent_type: Optional[str] = None, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[Dict]:
        """
        Get a summary of performance for all matching agents.
        """
        accounts = self.get_agent_accounts(agent_type)
        summary = []
        
        for acc in accounts:
            pnl_stats = self.calculate_pnl(acc.id)
            trades_count = self.db.query(func.count(Trade.id)).filter(Trade.account_id == acc.id).scalar()
            decisions_count = self.db.query(func.count(AIDecisionLog.id)).filter(AIDecisionLog.account_id == acc.id).scalar()
            
            summary.append({
                "account_id": acc.id,
                "agent_name": acc.name,
                "agent_type": acc.agent_type,
                "model": acc.model,
                "trades_count": trades_count,
                "decisions_count": decisions_count,
                **pnl_stats
            })
            
        return summary

