from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Dict, Optional
from datetime import datetime

from database.models import Account, AIDecisionLog, Trade, AgentTrace, Position, AgentMemory
from services.asset_calculator import calc_positions_value

import ast

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

    # ========== Memory-related queries ==========

    def get_memories(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, market: Optional[str] = None) -> List[AgentMemory]:
        """
        Get all memories for a specific account within a time range.

        Args:
            account_id: Account ID
            start_time: Start time filter (optional)
            end_time: End time filter (optional)
            market: Market filter, e.g. "CRYPTO" or "US" (optional)

        Returns:
            List of AgentMemory objects
        """
        query = self.db.query(AgentMemory).filter(AgentMemory.account_id == account_id)
        if market:
            query = query.filter(AgentMemory.market == market)
        if start_time:
            query = query.filter(AgentMemory.created_at >= start_time)
        if end_time:
            query = query.filter(AgentMemory.created_at <= end_time)
        return query.order_by(AgentMemory.created_at).all()

    def get_memory_stats(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None, market: Optional[str] = None) -> Dict:
        """
        Get memory statistics for an account.

        Returns:
            Dictionary with memory statistics including count, avg length, etc.
        """
        query = self.db.query(AgentMemory).filter(AgentMemory.account_id == account_id)
        if market:
            query = query.filter(AgentMemory.market == market)
        if start_time:
            query = query.filter(AgentMemory.created_at >= start_time)
        if end_time:
            query = query.filter(AgentMemory.created_at <= end_time)

        memories = query.all()

        if not memories:
            return {
                "total_count": 0,
                "avg_length": 0,
                "total_length": 0,
                "time_span_days": 0
            }

        total_length = sum(len(m.content) for m in memories)
        avg_length = total_length / len(memories) if memories else 0

        # Calculate time span
        time_span_days = 0
        if len(memories) > 1:
            first_time = min(m.created_at for m in memories)
            last_time = max(m.created_at for m in memories)
            time_span_days = (last_time - first_time).days

        return {
            "total_count": len(memories),
            "avg_length": round(avg_length, 2),
            "total_length": total_length,
            "time_span_days": time_span_days
        }

    def get_memory_tool_usage_from_traces(self, account_id: int, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> Dict:
        """
        Analyze memory tool usage from agent traces.
        Counts how many times memory_add and memory_search were called.

        Returns:
            Dictionary with memory tool usage statistics
        """
        traces = self.get_traces(account_id, start_time, end_time)

        memory_add_count = 0
        memory_search_count = 0

        for trace in traces:
            # Parse trace content to find tool calls
            import json
            try:
                if trace.role == "assistant" and trace.tool_calls:
                    # tool_calls is a JSON string, need to parse it first
                    tool_calls_list = json.loads(trace.tool_calls)
                    if isinstance(tool_calls_list, list):
                        for tool_call in tool_calls_list:
                            if isinstance(tool_call, str):
                                # Inner element is a Python dict string, use ast.literal_eval
                                tool_call = ast.literal_eval(tool_call)
                            tool_name = tool_call.get("function", ).get("name")
                            if tool_name == "memory_add":
                                memory_add_count += 1
                            elif tool_name == "memory_search":
                                memory_search_count += 1
            except Exception as e:
                continue

        return {
            "memory_add_count": memory_add_count,
            "memory_search_count": memory_search_count,
            "total_memory_operations": memory_add_count + memory_search_count
        }

    def get_memories_by_trace(self, trace_id: str) -> List[AgentMemory]:
        """
        Get all memories associated with a specific trace/decision session.

        Args:
            trace_id: The trace ID to filter by

        Returns:
            List of AgentMemory objects linked to this trace
        """
        return self.db.query(AgentMemory).filter(AgentMemory.trace_id == trace_id).all()
