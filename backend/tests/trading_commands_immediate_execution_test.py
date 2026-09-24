"""Completed workers are consumed immediately in completion order."""
from concurrent import futures
from threading import Lock
from types import SimpleNamespace

from benchmark.application.decisions.service import DecisionRoundService, RunDecisionRound


def test_agent_decision_processed_immediately_on_each_completed_future(monkeypatch):
    consumed = []

    class Completed:
        def __init__(self, account_id):
            self.account_id = account_id

        def result(self):
            consumed.append(self.account_id)
            return SimpleNamespace(termination_reason=SimpleNamespace(value="hold"))

    completed = {i: Completed(i) for i in (1, 2)}

    class Executor:
        def __init__(self, max_workers):
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, worker, account_id, prices, round_id, **kwargs):
            assert prices == {"BTC": 100.0} and round_id
            return completed[account_id]

    def as_completed(pending):
        assert set(pending) == set(completed.values())
        yield completed[2]
        assert consumed == [2], "A completed worker must be consumed before waiting for the next"
        yield completed[1]

    monkeypatch.setattr(futures, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(futures, "as_completed", as_completed)
    lock = Lock()
    result = DecisionRoundService(
        selector=lambda requested: [1, 2], prices=lambda: {"BTC": 100.0},
        worker=lambda *args, **kwargs: None, is_cancelled=lambda: False, lock=lock,
    ).run(RunDecisionRound(None, 2, "test"))
    assert consumed == [2, 1]
    assert result.processed_accounts == 2 and not result.errors
    assert not lock.locked()
