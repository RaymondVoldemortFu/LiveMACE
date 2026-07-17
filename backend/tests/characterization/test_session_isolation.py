from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace

from services import trading_commands


def test_sqlite_session_fixture_rolls_back_and_closes(sqlite_session_factory):
    from database.models import User

    with sqlite_session_factory() as session:
        session.add(User(username="temporary-user", is_active="true"))
        session.flush()
        assert session.query(User).filter(User.username == "temporary-user").count() == 1

    assert sqlite_session_factory.sessions[-1].was_closed is True

    with sqlite_session_factory() as verification:
        assert verification.query(User).filter(User.username == "temporary-user").count() == 0


def test_account_workers_run_in_parallel_with_distinct_closed_sessions(monkeypatch):
    sessions = []
    session_lock = Lock()
    barrier = Barrier(2)
    active_threads = set()

    class WorkerSession:
        closed = False

        def close(self):
            self.closed = True

    def session_factory():
        session = WorkerSession()
        with session_lock:
            sessions.append(session)
        return session

    def fake_agent(account, portfolio, prices, db, decision_round_id=None):
        active_threads.add(get_ident())
        barrier.wait(timeout=2)
        return {
            "operation": "hold",
            "protocol": "tool",
            "executed_trades": [],
            "reason": "characterization",
        }

    monkeypatch.setattr(trading_commands, "SessionLocal", session_factory)
    monkeypatch.setattr(
        trading_commands,
        "get_account",
        lambda db, account_id: SimpleNamespace(id=account_id, name=f"a{account_id}"),
    )
    monkeypatch.setattr(
        trading_commands,
        "_get_portfolio_data",
        lambda db, account: {"total_assets": 1000.0},
    )
    monkeypatch.setattr(trading_commands.AgentConfig, "USE_AGENT", True)
    monkeypatch.setattr(trading_commands, "call_agent_for_decision", fake_agent)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda account_id: trading_commands._collect_account_decision(
                    account_id,
                    {"BTC": 100.0},
                    "round-1",
                ),
                (1, 2),
            )
        )

    assert {item["account_id"] for item in results} == {1, 2}
    assert len(active_threads) == 2
    assert len(sessions) == 2
    assert len({id(session) for session in sessions}) == 2
    assert all(session.closed for session in sessions)

