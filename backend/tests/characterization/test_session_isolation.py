from __future__ import annotations


def test_sqlite_session_fixture_rolls_back_and_closes(sqlite_session_factory):
    from database.models import User

    with sqlite_session_factory() as session:
        session.add(User(username="temporary-user", is_active="true"))
        session.flush()
        assert session.query(User).filter(User.username == "temporary-user").count() == 1

    assert sqlite_session_factory.sessions[-1].was_closed is True

    with sqlite_session_factory() as verification:
        assert verification.query(User).filter(User.username == "temporary-user").count() == 0


# Active worker concurrency and short-session behavior are covered by
# test_wave3_decisions.test_worker_runs_real_builtin_runtime_with_short_sessions.
