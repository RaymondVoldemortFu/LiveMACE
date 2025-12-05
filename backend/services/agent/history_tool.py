import logging
import json
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from database.models import AIDecisionLog
from services.agent.tools import Tool

logger = logging.getLogger(__name__)

def get_decision_history(account_id: int, limit: int = 5, db: Session = None) -> List[Dict[str, Any]]:
    """
    Retrieves the history of AI decisions for a specific account.
    
    Args:
        account_id (int): The ID of the account to retrieve history for.
        limit (int): The number of recent decisions to retrieve. Default is 5.
        db (Session): The database session.
        
    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing decision details.
    """
    if not db:
        return []

    try:
        decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == account_id
        ).order_by(AIDecisionLog.decision_time.desc()).limit(limit).all()

        history = []
        for d in decisions:
            history.append({
                "time": d.decision_time.strftime("%Y-%m-%d %H:%M:%S"),
                "operation": d.operation,
                "symbol": d.symbol,
                "reason": d.reason,
                "executed": d.executed,
                "leverage": d.leverage
            })
        
        logger.info(f"Retrieved {len(history)} history records for account {account_id}")
        return history

    except Exception as e:
        logger.error(f"Failed to retrieve decision history: {e}")
        return []

class HistoryTool(Tool):
    def __init__(self, db: Session, account_id: int):
        super().__init__(
            name="get_history_decisions",
            description="Get the recent trading decision history for this account to understand past actions and reasoning.",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent decisions to retrieve (default 5, max 20)"
                    }
                },
                "required": []
            },
            func=lambda limit=5: get_decision_history(account_id, min(limit, 20), db)
        )

