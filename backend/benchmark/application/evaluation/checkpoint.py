"""Checkpoint computation, separate from scheduler registration."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CheckpointRunResult:
    created: int
    intervals: tuple[int, ...]


class CheckpointService:
    """Create due checkpoints. Callers register the schedule elsewhere."""

    def __init__(self, session_scope=None) -> None:
        self._session_scope = session_scope or _owned_session

    def run_due(self, intervals: tuple[int, ...], now: datetime) -> CheckpointRunResult:
        from services.evaluation.checkpoint_service import (
            _list_active_ai_accounts,
            create_checkpoint_if_due,
        )
        import logging

        logger = logging.getLogger(__name__)
        normalized = tuple(sorted({int(item) for item in intervals if int(item) > 0}))
        if not normalized:
            return CheckpointRunResult(created=0, intervals=())

        created = 0
        with self._session_scope() as db:
            accounts = _list_active_ai_accounts(db)
            for interval_seconds in normalized:
                for account in accounts:
                    try:
                        ckpt = create_checkpoint_if_due(
                            db,
                            account,
                            interval_seconds=interval_seconds,
                            now=now,
                        )
                    except Exception as exc:
                        logger.error(
                            "Checkpoint creation failed for account %s (%s) interval=%ss: %s",
                            account.id,
                            account.name,
                            interval_seconds,
                            exc,
                            exc_info=True,
                        )
                        continue
                    if ckpt is not None:
                        created += 1
            if created:
                db.commit()
            else:
                db.rollback()
        return CheckpointRunResult(created=created, intervals=normalized)


@contextmanager
def _owned_session() -> Iterator[Session]:
    from database.connection import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
