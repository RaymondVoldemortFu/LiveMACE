"""
Tool - Provides comprehensive trading decision history with P&L tracking.

This tool gives the agent visibility into:
1. Past decisions and their reasoning
2. Account total assets changes over time
3. Individual position P&L for each decision
4. Overall portfolio performance
"""
import logging
import json
from typing import Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database.models import AIDecisionLog, Order, Position, Account, CRYPTO_TAKER_FEE_RATE
from services.agent.tools import Tool
from services.market_data import get_last_price
from services.asset_calculator import calc_positions_market_value

logger = logging.getLogger(__name__)

def calculate_position_pnl(position: Position, current_price: float) -> Dict[str, Any]:
    """
    Calculate P&L for a single position, including fees and interest.

    For LONG positions: PnL = (current_price - avg_cost) * quantity - fees - interest
    For SHORT positions: PnL = (avg_cost - current_price) * quantity - fees - interest

    Fees include:
    - Accumulated interest (for leveraged positions)
    - Estimated opening fees (based on avg_cost, same taker fee rate)
    - Estimated closing fees (if position were closed now)

    Returns dict with detailed P&L information.
    """
    try:
        quantity = float(position.quantity)
        avg_cost = float(position.avg_cost)
        leverage = position.leverage if position.leverage else 1
        accumulated_interest = float(position.accumulated_interest) if position.accumulated_interest else 0

        # Calculate gross unrealized P&L (before fees)
        if position.side == "SHORT":
            gross_pnl = (avg_cost - current_price) * quantity
        else:  # LONG
            gross_pnl = (current_price - avg_cost) * quantity

        # Estimate opening fee (based on entry notional value)
        opening_fee = avg_cost * quantity * CRYPTO_TAKER_FEE_RATE

        # Estimate closing fee (based on current notional value)
        closing_fee = current_price * quantity * CRYPTO_TAKER_FEE_RATE

        total_fees = opening_fee + closing_fee

        # Net unrealized P&L = Gross P&L - Accumulated Interest - Total Fees
        net_unrealized_pnl = gross_pnl - accumulated_interest - total_fees

        # Calculate P&L percentage (based on margin used, not notional value)
        margin_used = (quantity * avg_cost) / leverage
        pnl_percent = (net_unrealized_pnl / margin_used * 100) if margin_used > 0 else 0

        return {
            "symbol": position.symbol,
            "side": position.side or "LONG",
            "quantity": quantity,
            "avg_cost": round(avg_cost, 2),
            "current_price": round(current_price, 2),
            "leverage": leverage,
            "gross_pnl": round(gross_pnl, 2),
            "accumulated_interest": round(accumulated_interest, 2),
            "estimated_opening_fee": round(opening_fee, 2),
            "estimated_closing_fee": round(closing_fee, 2),
            "net_unrealized_pnl": round(net_unrealized_pnl, 2),
            "pnl_percent": round(pnl_percent, 2),
            "margin_used": round(margin_used, 2)
        }
    except Exception as e:
        logger.error(f"Error calculating position P&L: {e}")
        return None


def get_decision_history(account_id: int, limit: int = 5, db: Session = None) -> Dict[str, Any]:
    """
    Enhanced version: Returns comprehensive decision history with P&L tracking.

    For each decision, provides:
    - Decision details (operation, symbol, reason, leverage)
    - Account total assets at decision time vs now
    - Overall portfolio P&L since that decision
    - Individual position P&L (if position still open)
    - Execution status and order details

    Args:
        account_id: The account ID
        limit: Number of recent decisions to retrieve (max 20)
        db: Database session

    Returns:
        Dict with status, current account state, and decision history with P&L
    """
    if not db:
        return {"status": "error", "message": "Database session not available"}

    try:
        # Get current account state
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            return {"status": "error", "message": f"Account {account_id} not found"}

        # Calculate current total assets (equity = cash + positions market value)
        current_cash = float(account.current_cash)
        current_positions_value = calc_positions_market_value(db, account_id)
        current_total_assets = current_cash + current_positions_value

        # Get current positions for P&L calculation
        current_positions = db.query(Position).filter(
            Position.account_id == account_id,
            Position.quantity > 0
        ).all()

        # Get recent decisions
        decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == account_id
        ).order_by(AIDecisionLog.decision_time.desc()).limit(min(limit, 20)).all()

        if not decisions:
            return {
                "status": "success",
                "message": "No decision history found",
                "current_account": {
                    "total_assets": round(current_total_assets, 2),
                    "cash": round(current_cash, 2),
                    "positions_value": round(current_positions_value, 2)
                },
                "decisions": []
            }

        # Build decision history with P&L tracking
        history = []
        for d in decisions:
            # Calculate time since decision (use UTC to match SQLite's current_timestamp)
            decision_time = d.decision_time.replace(tzinfo=timezone.utc) if d.decision_time.tzinfo is None else d.decision_time
            time_diff = datetime.now(timezone.utc) - decision_time
            hours_ago = time_diff.total_seconds() / 3600

            # Basic decision info
            decision_info = {
                "decision_id": d.id,
                "time": d.decision_time.strftime("%Y-%m-%d %H:%M:%S"),
                "hours_ago": round(hours_ago, 1),
                "operation": d.operation,
                "symbol": d.symbol,
                "direction": d.direction if d.direction else "unknown",
                "reason": d.reason,
                "leverage": d.leverage,
                "executed": d.executed,
                "target_portion": float(d.target_portion) if d.target_portion else None
            }

            # Account performance since this decision
            decision_total_assets = float(d.total_balance) if d.total_balance is not None else 0.0
            assets_change = current_total_assets - decision_total_assets
            assets_change_percent = (assets_change / decision_total_assets * 100) if decision_total_assets > 0 else 0

            decision_info["account_performance"] = {
                "total_assets_at_decision": round(decision_total_assets, 2),
                "current_total_assets": round(current_total_assets, 2),
                "assets_change": round(assets_change, 2),
                "assets_change_percent": round(assets_change_percent, 2)
            }

            # Execution details if order was executed
            if d.executed == "true":
                # Use execution_price/execution_quantity if available, otherwise fall back to Order table
                if d.execution_price is not None:
                    decision_info["execution"] = {
                        "price": float(d.execution_price),
                        "quantity": float(d.execution_quantity) if d.execution_quantity is not None else None,
                        "order_id": d.order_id
                    }
                elif d.order_id:
                    order = db.query(Order).filter(Order.id == d.order_id).first()
                    if order:
                        decision_info["execution"] = {
                            "order_id": order.id,
                            "price": float(order.price) if order.price else None,
                            "quantity": float(order.quantity),
                            "status": order.status,
                            "filled_quantity": float(order.filled_quantity)
                        }

                # Position P&L: independent of execution source — check for any executed "open" with live position
                if d.operation == "open" and d.symbol:
                    position = next((p for p in current_positions if p.symbol == d.symbol), None)
                    if position:
                        try:
                            current_price = get_last_price(d.symbol)
                            pnl_info = calculate_position_pnl(position, current_price)
                            if pnl_info:
                                decision_info["position_pnl"] = pnl_info
                        except Exception as e:
                            logger.warning(f"Could not calculate P&L for {d.symbol}: {e}")

            history.append(decision_info)

        # Summary statistics
        logger.info(f"Retrieved {len(history)} enhanced history records for account {account_id}")

        return {
            "status": "success",
            "message": f"Retrieved {len(history)} decisions with P&L tracking",
            "current_account": {
                "total_assets": round(current_total_assets, 2),
                "cash": round(current_cash, 2),
                "positions_value": round(current_positions_value, 2),
                "initial_capital": float(account.initial_capital)
            },
            "overall_performance": {
                "total_pnl": round(current_total_assets - float(account.initial_capital), 2),
                "total_pnl_percent": round((current_total_assets - float(account.initial_capital)) / float(account.initial_capital) * 100, 2) if account.initial_capital > 0 else 0
            },
            "decisions": history
        }

    except Exception as e:
        logger.error(f"Failed to retrieve decision history: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


class HistoryTool(Tool):
    """
    Enhanced History Tool for trading agents.

    Provides comprehensive decision history including:
    - Past decisions and reasoning
    - Account total assets changes over time
    - Individual position P&L tracking
    - Overall portfolio performance metrics

    This allows the agent to:
    1. Learn from past decisions (what worked, what didn't)
    2. Track performance of current positions
    3. Understand account-level P&L trends
    4. Make informed decisions based on historical context
    """
    def __init__(self, db: Session, account_id: int):
        super().__init__(
            name="get_history_decisions",
            description="""Get your recent trading decision history with P&L tracking. Call this EARLY in your workflow (after get_account_state) to understand what you did recently and avoid repeating mistakes.

Returns:
- Each decision: operation, symbol, direction, leverage, reason, whether it was executed
- Account performance since each decision: total assets then vs now, change amount and percent
- Position P&L for open positions from past decisions: gross P&L, fees, interest, net unrealized P&L, return on margin
- Execution details: actual fill price and quantity

When to call:
- At the start of each decision cycle to review recent actions
- Before opening a position on a symbol you recently traded
- When you want to evaluate whether your recent strategy is working

Do NOT call this repeatedly in the same decision cycle. One call with limit=5 is usually sufficient.""",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent decisions to retrieve (default 5, max 20)",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20
                    }
                },
                "required": []
            },
            func=lambda limit=5: get_decision_history(account_id, min(limit, 20), db),
            metadata={"tier": "required"}
        )

