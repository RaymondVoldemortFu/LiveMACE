import ast
import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import AIDecisionLog, Account, AgentTrace, RuntimeEvent


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
        rows = self.db.query(RuntimeEvent).filter(RuntimeEvent.trace_id == trace_id).order_by(RuntimeEvent.sequence, RuntimeEvent.id).all()
        events = [{"id": row.id, "type": row.event_type, "account_id": row.account_id,
                   "decision_round_id": row.decision_round_id, "payload": json.loads(row.payload),
                   "created_at": row.created_at} for row in rows]
        if not traces:
            decision = self.db.query(AIDecisionLog).filter(AIDecisionLog.trace_id == trace_id).first()
            if not decision and not events:
                raise TraceNotFoundError("Trace not found")
            return {"trace_id": trace_id, "steps": [], "events": events, "schema_version": 1}
        return {
            "trace_id": trace_id,
            "events": events,
            "schema_version": 1,
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
        history = self.get_trace_history(account_id, 1)
        return {"trace_id": history[0]["trace_id"] if history else None}

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
        known = {item["trace_id"] for item in history}
        for model in (AgentTrace, RuntimeEvent):
            rows = (self.db.query(model.trace_id.label("trace_id"), func.max(model.created_at).label("timestamp"))
                .filter(model.account_id == account_id, model.trace_id.isnot(None))
                .group_by(model.trace_id).order_by(func.max(model.created_at).desc()).limit(limit).all())
            for row in rows:
                if row.trace_id not in known:
                    history.append({"trace_id": row.trace_id, "timestamp": row.timestamp,
                        "operation": "trace", "symbol": None, "reason": "Agent runtime session"})
                    known.add(row.trace_id)
        history.sort(key=lambda item: item["timestamp"], reverse=True)
        return history[:limit]
