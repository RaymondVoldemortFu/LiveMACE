"""Host-owned limits shared by main, search and audit LLM calls in one worker."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

_current = ContextVar("agent_request_scope", default=None)


@dataclass
class RequestScope:
    deadline_at: datetime
    is_cancelled: Callable[[], bool]
    max_calls: int
    max_output_tokens: int
    events: object
    calls: int = 0

    def before_attempt(self, kwargs):
        remaining = (self.deadline_at - datetime.now(timezone.utc)).total_seconds()
        if self.is_cancelled() or remaining <= 0:
            raise TimeoutError("Agent request deadline exceeded or cancelled")
        if self.calls >= self.max_calls:
            raise RuntimeError("LLM_BUDGET_EXCEEDED")
        self.calls += 1
        output = dict(kwargs)
        output["timeout"] = min(float(output.get("timeout", remaining)), remaining)
        for key in ("max_tokens", "max_completion_tokens"):
            if key in output:
                output[key] = min(output[key], self.max_output_tokens)
        self.events.record(
            "llm.attempt",
            {
                "model": output["model"],
                "attempt_number": self.calls,
                "timeout_seconds": output["timeout"],
            },
        )
        return output


@contextmanager
def use_request_scope(scope):
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def current_request_scope():
    return _current.get()
