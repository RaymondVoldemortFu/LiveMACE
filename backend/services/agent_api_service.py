import ast
import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import AIDecisionLog, Account, AgentTrace


class TraceNotFoundError(LookupError):
    pass


class AccountNotFoundError(LookupError):
    pass


def _parse_maybe_json(raw: Any) -> Any:
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


class AgentApiService:
    def __init__(self, db: Session):
        self.db = db

    def get_trace(self, trace_id: str):
        traces = (
            self.db.query(AgentTrace)
            .filter(AgentTrace.trace_id == trace_id)
            .order_by(AgentTrace.step_number)
            .all()
        )
        if not traces:
            decision = self.db.query(AIDecisionLog).filter(AIDecisionLog.trace_id == trace_id).first()
            if not decision:
                raise TraceNotFoundError("Trace not found")
            return {"trace_id": trace_id, "steps": []}
        return {
            "trace_id": trace_id,
            "steps": [
                {
                    "step_number": trace.step_number,
                    "role": trace.role,
                    "content": trace.content,
                    "tool_calls": _parse_maybe_json(trace.tool_calls),
                    "tool_output": _parse_maybe_json(trace.tool_output)
                    if trace.role == "tool"
                    else trace.tool_output,
                    "created_at": trace.created_at,
                }
                for trace in traces
            ],
        }

    def get_latest_trace(self, account_id: int):
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise AccountNotFoundError("Account not found")
        latest_trace = (
            self.db.query(AgentTrace)
            .filter(AgentTrace.account_id == account_id)
            .order_by(AgentTrace.created_at.desc())
            .first()
        )
        if latest_trace:
            return {"trace_id": latest_trace.trace_id}
        latest_decision = (
            self.db.query(AIDecisionLog)
            .filter(AIDecisionLog.account_id == account_id)
            .order_by(AIDecisionLog.decision_time.desc())
            .first()
        )
        return {"trace_id": latest_decision.trace_id if latest_decision and latest_decision.trace_id else None}

    def get_trace_history(self, account_id: int, limit: int):
        decisions = (
            self.db.query(AIDecisionLog)
            .filter(AIDecisionLog.account_id == account_id, AIDecisionLog.trace_id.isnot(None))
            .order_by(AIDecisionLog.decision_time.desc())
            .limit(limit)
            .all()
        )
        history = []
        for decision in decisions:
            reason_text = str(decision.reason) if decision.reason is not None else ""
            history.append(
                {
                    "trace_id": decision.trace_id,
                    "timestamp": decision.decision_time,
                    "operation": decision.operation,
                    "symbol": decision.symbol,
                    "reason": reason_text[:50] + "..." if reason_text else "",
                }
            )
        if not history:
            rows = (
                self.db.query(
                    AgentTrace.trace_id.label("trace_id"),
                    func.max(AgentTrace.created_at).label("timestamp"),
                )
                .filter(AgentTrace.account_id == account_id, AgentTrace.trace_id.isnot(None))
                .group_by(AgentTrace.trace_id)
                .order_by(func.max(AgentTrace.created_at).desc())
                .limit(limit)
                .all()
            )
            history.extend(
                {
                    "trace_id": row.trace_id,
                    "timestamp": row.timestamp,
                    "operation": "trace",
                    "symbol": None,
                    "reason": "Agent trace session",
                }
                for row in rows
            )
        return history
