from __future__ import annotations

from contextlib import contextmanager
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Characterization tests must never probe the developer's configured MySQL DB.
os.environ["DATABASE_URL"] = "sqlite://"

from database.connection import Base  # noqa: E402
from database import models as _models  # noqa: E402,F401


class TrackingSession(Session):
    was_closed = False

    def close(self) -> None:
        self.was_closed = True
        super().close()


@pytest.fixture(scope="session")
def sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine):
    connection = sqlite_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, class_=TrackingSession, expire_on_commit=False)
    session = factory()
    yield session
    session.rollback()
    session.close()
    if transaction.is_active:
        transaction.rollback()
    connection.close()


@pytest.fixture
def sqlite_session_factory(sqlite_engine):
    sessions: list[TrackingSession] = []
    factory = sessionmaker(bind=sqlite_engine, class_=TrackingSession, expire_on_commit=False)

    @contextmanager
    def open_session():
        session = factory()
        sessions.append(session)
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    open_session.sessions = sessions
    return open_session
