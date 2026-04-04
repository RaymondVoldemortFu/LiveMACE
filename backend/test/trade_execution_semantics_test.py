import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent import trade_execution_tool as te


def test_execute_close_crypto_spot_forces_sell(monkeypatch):
    captured = {}

    def fake_place_and_execute_crypto(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=1, order_no="O1")

    monkeypatch.setattr(te, "place_and_execute_crypto", fake_place_and_execute_crypto)

    account = SimpleNamespace(id=11)
    position = SimpleNamespace(leverage=1)
    te._execute_close(
        db=object(),
        account=account,
        symbol="BTC",
        market="CRYPTO",
        direction="short",
        quantity=0.1,
        position=position,
    )

    assert captured["side"] == "SELL"
    assert captured["leverage"] == 1


def test_execute_close_crypto_leveraged_keeps_direction_mapping(monkeypatch):
    captured = {}

    def fake_place_and_execute_crypto(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=2, order_no="O2")

    monkeypatch.setattr(te, "place_and_execute_crypto", fake_place_and_execute_crypto)

    account = SimpleNamespace(id=22)
    position = SimpleNamespace(leverage=5)
    te._execute_close(
        db=object(),
        account=account,
        symbol="BTC",
        market="CRYPTO",
        direction="short",
        quantity=0.2,
        position=position,
    )

    # Leveraged SHORT close => BUY
    assert captured["side"] == "BUY"


class _FakeQuery:
    def __init__(self, model, account):
        self.model = model
        self.account = account

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if getattr(self.model, "__name__", "") == "Account":
            return self.account
        return None


class _FakeDb:
    def __init__(self, account):
        self._account = account

    def query(self, model):
        return _FakeQuery(model, self._account)


def test_execute_trade_tool_rejects_crypto_short_in_spot(monkeypatch):
    monkeypatch.setattr(te, "get_last_price", lambda symbol, market: 100.0)

    account = SimpleNamespace(id=1, current_cash=1000)
    db = _FakeDb(account)

    result = te.execute_trade_tool(
        db=db,
        account_id=1,
        operation="open",
        symbol="BTC",
        market="CRYPTO",
        direction="short",
        size_mode="portion",
        target_portion_of_balance=0.2,
        leverage=1,
        reason="unit test",
    )

    assert result["executed"] is False
    assert "requires leverage > 1" in result["error"]

