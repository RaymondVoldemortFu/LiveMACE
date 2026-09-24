"""Own the checkpoint transaction and isolate individual failed calculations."""

from contextlib import contextmanager


class SqlAlchemyCheckpointBatch:
    def __init__(self, db):
        self._db = db

    def list_accounts(self):
        from services.evaluation.checkpoint_service import _list_active_ai_accounts

        return _list_active_ai_accounts(self._db)

    def create(self, account, interval, now):
        from services.evaluation.checkpoint_service import create_checkpoint_if_due

        with self._db.begin_nested():
            return (
                create_checkpoint_if_due(self._db, account, interval, now) is not None
            )

    def commit(self):
        self._db.commit()


@contextmanager
def checkpoint_batch(session_scope=None):
    if session_scope is None:
        from database.connection import SessionLocal

        session_scope = SessionLocal
    with session_scope() as db:
        try:
            yield SqlAlchemyCheckpointBatch(db)
        finally:
            db.rollback()
