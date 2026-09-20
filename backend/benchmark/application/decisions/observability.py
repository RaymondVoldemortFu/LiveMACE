"""Persistence adapters for the public Agent and Tool event sinks."""

import json
import logging
import re
from uuid import uuid4

from benchmark.contracts import to_jsonable
from benchmark.tools.invoker import redact_tool_value
from database.connection import SessionLocal
from database.models import AgentTrace, RuntimeEvent

logger = logging.getLogger(__name__)


def redact(value, secrets=()):
    value = redact_tool_value(to_jsonable(value))
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(redact(parsed, secrets), ensure_ascii=False)
        value = re.sub(r"(?i)(bearer\s+)[\w.\-/+=]+", r"\1[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
        return value
    return value


class PersistentEventSink:
    def __init__(self, context, *, secrets=(), session_factory=SessionLocal):
        self.context = context
        self.secrets = secrets
        self.session_factory = session_factory
        self.step = 0
        self.sequence = 0

    def emit(self, event):
        self.record(event.type, to_jsonable(event))

    def record(self, event_type, payload):
        # Observability failure must never replay a trade or replace its outcome.
        try:
            self.sequence += 1
            payload = redact(payload, self.secrets)
            with self.session_factory() as db:
                db.add(
                    RuntimeEvent(
                        id=str(uuid4()),
                        account_id=self.context.account_id,
                        decision_round_id=self.context.decision_round_id,
                        trace_id=self.context.trace_id,
                        event_type=event_type,
                        sequence=self.sequence,
                        payload=json.dumps(payload, ensure_ascii=False),
                    )
                )
                if event_type == "agent.step":
                    self.step += 1
                    meta = payload["metadata"]
                    content = meta.get("content")
                    if content is not None and not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False)
                    db.add(
                        AgentTrace(
                            trace_id=self.context.trace_id,
                            account_id=self.context.account_id,
                            step_number=self.step,
                            role=meta["role"][:20],
                            content=content,
                            tool_calls=json.dumps(
                                meta["tool_calls"], ensure_ascii=False
                            )
                            if meta.get("tool_calls")
                            else None,
                            tool_output=content if meta["role"] == "tool" else None,
                        )
                    )
                db.commit()
        except Exception as exc:
            logger.warning("Runtime event persistence failed: %s", type(exc).__name__)
