"""Persist and log the same redacted runtime event identity."""

import json
import logging
from uuid import uuid4
from benchmark.contracts import to_jsonable
from benchmark.application.decisions.observability import redact
from database.connection import SessionLocal
from database.models import AgentTrace, RuntimeEvent

logger = logging.getLogger(__name__)


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
        self.sequence += 1
        event_id = str(uuid4())
        payload = redact(payload, self.secrets)
        record = dict(
            event_id=event_id,
            event_type=event_type,
            account_id=self.context.account_id,
            trace_id=self.context.trace_id,
            decision_round_id=self.context.decision_round_id,
            sequence=self.sequence,
            payload=payload,
        )
        logger.info(
            "runtime.event %s",
            json.dumps(record, ensure_ascii=False),
            extra={"runtime_event": record},
        )
        try:
            with self.session_factory() as db:
                db.add(
                    RuntimeEvent(
                        id=event_id,
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
            logger.warning(
                "Runtime event persistence failed: %s event_id=%s",
                type(exc).__name__,
                event_id,
                extra={"event_id": event_id},
            )
