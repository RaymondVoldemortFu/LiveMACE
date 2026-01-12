from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
import json

from database.connection import get_db
from database.models import AgentTrace, AIDecisionLog, Account

router = APIRouter(prefix="/api/agent", tags=["agent"])

@router.get("/trace/{trace_id}")
def get_agent_trace(trace_id: str, db: Session = Depends(get_db)):
    """Get detailed execution trace for a specific session"""
    # Query first to get data, avoiding lazy loading issues if session closes
    traces_query = db.query(AgentTrace).filter(AgentTrace.trace_id == trace_id).order_by(AgentTrace.step_number)
    traces = traces_query.all()
    
    if not traces:
        # check if it exists in decision logs (maybe empty trace?)
        decision = db.query(AIDecisionLog).filter(AIDecisionLog.trace_id == trace_id).first()
        if not decision:
            raise HTTPException(status_code=404, detail="Trace not found")
        return {"trace_id": trace_id, "steps": []}

    steps = []
    for t in traces:
        steps.append({
            "step_number": t.step_number,
            "role": t.role,
            "content": t.content,
            "tool_calls": json.loads(t.tool_calls) if t.tool_calls else None,
            "tool_output": json.loads(t.tool_output) if t.tool_output and t.role == "tool" else t.tool_output,
            "created_at": t.created_at
        })
    
    return {
        "trace_id": trace_id,
        "steps": steps
    }

@router.get("/latest/{account_id}")
def get_latest_trace(account_id: int, db: Session = Depends(get_db)):
    """Get the latest trace ID for an account"""
    # Check account exists
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Try to find latest from AgentTrace (more accurate for running sessions)
    latest_trace = db.query(AgentTrace).filter(AgentTrace.account_id == account_id).order_by(AgentTrace.created_at.desc()).first()
    
    if latest_trace:
        return {"trace_id": latest_trace.trace_id}
        
    # Fallback to decision log
    latest_decision = db.query(AIDecisionLog).filter(AIDecisionLog.account_id == account_id).order_by(AIDecisionLog.decision_time.desc()).first()
    
    if latest_decision and latest_decision.trace_id:
        return {"trace_id": latest_decision.trace_id}
        
    return {"trace_id": None}

@router.get("/history/{account_id}")
def get_trace_history(account_id: int, limit: int = 20, db: Session = Depends(get_db)):
    """Get history of agent execution traces"""
    decisions = db.query(AIDecisionLog).filter(
        AIDecisionLog.account_id == account_id,
        AIDecisionLog.trace_id.isnot(None)
    ).order_by(AIDecisionLog.decision_time.desc()).limit(limit).all()
    
    history = []
    for d in decisions:
        history.append({
            "trace_id": d.trace_id,
            "timestamp": d.decision_time,
            "operation": d.operation,
            "symbol": d.symbol,
            "reason": d.reason[:50] + "..." if d.reason else ""
        })
        
    return history

