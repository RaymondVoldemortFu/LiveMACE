from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Dict, Any
import json
import ast

from database.connection import get_db
from database.models import AgentTrace, AIDecisionLog, Account

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _parse_maybe_json(raw: Any) -> Any:
    """
    Parse JSON-like payloads stored in trace fields.
    Falls back to Python literal parsing for legacy rows and returns
    original text when parsing fails.
    """
    if raw is None or not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    try:
        return ast.literal_eval(text)
    except Exception:
        return raw

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
            "tool_calls": _parse_maybe_json(t.tool_calls),
            "tool_output": _parse_maybe_json(t.tool_output) if t.role == "tool" else t.tool_output,
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
        reason_text = ""
        if d.reason is not None:
            reason_text = str(d.reason)
        history.append({
            "trace_id": d.trace_id,
            "timestamp": d.decision_time,
            "operation": d.operation,
            "symbol": d.symbol,
            "reason": reason_text[:50] + "..." if reason_text else ""
        })

    # Backward compatibility:
    # older tool-mode runs may have trace rows but no trace_id on decision logs.
    if not history:
        trace_rows = (
            db.query(
                AgentTrace.trace_id.label("trace_id"),
                func.max(AgentTrace.created_at).label("timestamp"),
            )
            .filter(
                AgentTrace.account_id == account_id,
                AgentTrace.trace_id.isnot(None),
            )
            .group_by(AgentTrace.trace_id)
            .order_by(func.max(AgentTrace.created_at).desc())
            .limit(limit)
            .all()
        )
        for row in trace_rows:
            history.append(
                {
                    "trace_id": row.trace_id,
                    "timestamp": row.timestamp,
                    "operation": "trace",
                    "symbol": None,
                    "reason": "Agent trace session",
                }
            )

    return history

