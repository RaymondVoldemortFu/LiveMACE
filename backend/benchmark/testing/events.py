"""Small recording event sinks for extension and contract tests.

The runtime event protocols deliberately only require an ``emit`` method.  A
single recording implementation can therefore be used for Agent and Tool
events without importing the application tracing stack.
"""

from __future__ import annotations

from threading import RLock
from typing import Any, Iterable, Iterator


class FakeEventSink:
    """A thread-safe, in-memory event sink.

    Events are kept as the original immutable DTO objects.  The public list is
    useful in assertions while ``snapshot`` gives callers a stable copy when a
    test emits from more than one worker.
    """

    def __init__(self, events: Iterable[Any] = ()) -> None:
        self.events = list(events)
        self._lock = RLock()

    def emit(self, event: Any) -> None:
        with self._lock:
            self.events.append(event)

    def snapshot(self) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self.events)

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(getattr(event, "type", "") for event in self.snapshot())

    def clear(self) -> None:
        with self._lock:
            self.events.clear()

    def __iter__(self) -> Iterator[Any]:
        return iter(self.snapshot())

    def __len__(self) -> int:
        with self._lock:
            return len(self.events)


# Descriptive aliases make the same fake convenient in examples that call it
# a recording sink or a Tool-specific sink.
RecordingEventSink = FakeEventSink
FakeToolEventSink = FakeEventSink


__all__ = [
    "FakeEventSink",
    "FakeToolEventSink",
    "RecordingEventSink",
]
