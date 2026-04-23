import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import trading_commands


class _DummyDB:
    def rollback(self):
        return None

    def close(self):
        return None


class _DummyAccount:
    def __init__(self, account_id: int, name: str, agent_type: str):
        self.id = account_id
        self.name = name
        self.agent_type = agent_type


def test_baseline_loop_not_blocked_by_ai_loop_lock(monkeypatch):
    called = {"baseline_load": 0}

    monkeypatch.setattr(trading_commands, "SessionLocal", lambda: _DummyDB())

    def _fake_load_baseline_accounts(_db):
        called["baseline_load"] += 1
        return []

    monkeypatch.setattr(trading_commands, "_load_baseline_accounts", _fake_load_baseline_accounts)

    acquired = trading_commands._ai_trade_run_lock.acquire(blocking=False)
    assert acquired is True
    try:
        trading_commands.place_baseline_driven_order()
    finally:
        trading_commands._ai_trade_run_lock.release()

    assert called["baseline_load"] == 1


def test_ai_loop_does_not_execute_baseline_accounts(monkeypatch):
    monkeypatch.setattr(trading_commands, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(
        trading_commands,
        "_load_trading_accounts",
        lambda _db: [_DummyAccount(1, "buy_hold", "buy_hold")],
    )
    monkeypatch.setattr(
        trading_commands,
        "_get_market_prices",
        lambda symbols, market, suppress_symbol_warnings=False: {"BTC": 1.0},
    )

    def _baseline_should_not_run(*args, **kwargs):
        raise AssertionError("baseline must not run inside AI loop")

    monkeypatch.setattr(trading_commands._buy_hold_baseline, "run_tick", _baseline_should_not_run)
    monkeypatch.setattr(trading_commands._grid_baseline, "run_tick", _baseline_should_not_run)

    trading_commands.place_ai_driven_crypto_order()
