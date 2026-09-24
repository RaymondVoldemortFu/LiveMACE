"""Checkpoint computation, separate from scheduler registration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, ContextManager, Callable


@dataclass(frozen=True)
class CheckpointRunResult:
    created: int
    intervals: tuple[int, ...]


class CheckpointBatch(Protocol):
    def list_accounts(self) -> list: ...
    def create(self, account, interval: int, now: datetime) -> bool: ...
    def commit(self) -> None: ...


class CheckpointService:
    """Coordinate idempotent checkpoints through a transaction capability."""

    def __init__(
        self, batch_factory: Callable[[], ContextManager[CheckpointBatch]] | None = None
    ):
        if batch_factory is None:
            from benchmark.persistence.checkpoints import checkpoint_batch

            batch_factory = checkpoint_batch
        self._batch_factory = batch_factory

    def run_due(self, intervals: tuple[int, ...], now: datetime) -> CheckpointRunResult:
        import logging

        normalized = tuple(sorted({int(item) for item in intervals if int(item) > 0}))
        if not normalized:
            return CheckpointRunResult(0, ())
        created = 0
        with self._batch_factory() as batch:
            accounts = batch.list_accounts()
            for interval in normalized:
                for account in accounts:
                    try:
                        created += int(batch.create(account, interval, now))
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Checkpoint failed account=%s interval=%s",
                            account.id,
                            interval,
                        )
            if created:
                batch.commit()
        return CheckpointRunResult(created, normalized)
