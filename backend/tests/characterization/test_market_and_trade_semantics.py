from __future__ import annotations

from decimal import Decimal

import pytest

from database.models import Account, Order, Position, Trade, User
from services import order_matching
from services.agent import trade_execution_tool
from tests.fakes import FakeMarketProvider


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (FakeMarketProvider({"BTC": 100.0}), 100.0),
        (FakeMarketProvider({"BTC": 0.0}), 0.0),
        (FakeMarketProvider({"BTC": None}), None),
    ],
)
def test_fake_market_price_modes(provider, expected):
    assert provider.get_last_price("BTC") == expected


def test_fake_market_error_and_us_closed_modes():
    with pytest.raises(RuntimeError, match="offline"):
        FakeMarketProvider(error=RuntimeError("offline")).get_last_price("BTC")
    assert FakeMarketProvider(us_open=False).get_market_status("AAPL", "US") == {
        "is_trading": False
    }


def test_market_buy_and_sell_preserve_ledger_state(db_session, monkeypatch):
    monkeypatch.setattr(order_matching, "get_last_price", lambda symbol, market: 100.0)
    user = User(username="ledger-user", is_active="true")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        name="ledger-account",
        account_type="MANUAL",
        initial_capital=1000,
        current_cash=1000,
        frozen_cash=0,
        is_active="true",
    )
    db_session.add(account)
    db_session.commit()

    buy = order_matching.create_order(
        db_session, account, "BTC", "Bitcoin", "BUY", "MARKET", None, 1.0
    )
    assert order_matching.check_and_execute_order(db_session, buy) is True

    db_session.refresh(account)
    position = db_session.query(Position).filter_by(account_id=account.id, symbol="BTC").one()
    assert buy.status == "FILLED"
    assert Decimal(str(account.current_cash)) == Decimal("899.900000")
    assert Decimal(str(position.quantity)) == Decimal("1.00000000")
    assert db_session.query(Trade).filter_by(order_id=buy.id).count() == 1

    sell = order_matching.create_order(
        db_session, account, "BTC", "Bitcoin", "SELL", "MARKET", None, 1.0
    )
    assert order_matching.check_and_execute_order(db_session, sell) is True

    db_session.refresh(account)
    db_session.refresh(position)
    assert sell.status == "FILLED"
    assert Decimal(str(account.current_cash)) == Decimal("999.800000")
    assert Decimal(str(position.quantity)) == Decimal("0E-8")
    assert db_session.query(Order).filter_by(account_id=account.id).count() == 2
    assert db_session.query(Trade).filter_by(account_id=account.id).count() == 2


def test_limit_pending_scheduler_fill_and_cancel_preserve_ledger_state(db_session, monkeypatch):
    market_price = {"value": 100.0}
    monkeypatch.setattr(
        order_matching,
        "get_last_price",
        lambda symbol, market: market_price["value"],
    )
    user = User(username="limit-user", is_active="true")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        name="limit-account",
        account_type="MANUAL",
        initial_capital=1000,
        current_cash=1000,
        frozen_cash=0,
        is_active="true",
    )
    db_session.add(account)
    db_session.commit()

    fill_later = order_matching.create_order(
        db_session, account, "BTC", "Bitcoin", "BUY", "LIMIT", 90.0, 1.0
    )
    cancel = order_matching.create_order(
        db_session, account, "ETH", "Ethereum", "BUY", "LIMIT", 50.0, 1.0
    )
    db_session.commit()

    assert order_matching.check_and_execute_order(db_session, fill_later) is False
    assert fill_later.status == "PENDING"
    assert db_session.query(Trade).filter_by(account_id=account.id).count() == 0
    assert order_matching.cancel_order(db_session, cancel) is True
    assert cancel.status == "CANCELLED"

    market_price["value"] = 80.0
    assert order_matching.process_all_pending_orders(db_session) == (1, 1)

    db_session.refresh(account)
    db_session.refresh(fill_later)
    position = db_session.query(Position).filter_by(account_id=account.id, symbol="BTC").one()
    assert fill_later.status == "FILLED"
    assert Decimal(str(account.current_cash)) == Decimal("919.90")
    assert Decimal(str(position.quantity)) == Decimal("1.00000000")
    assert db_session.query(Order).filter_by(account_id=account.id).count() == 2
    assert db_session.query(Trade).filter_by(account_id=account.id).count() == 1


def test_execute_trade_size_modes_are_characterized():
    account = type("Account", (), {"current_cash": 1000.0})()
    assert trade_execution_tool._calc_open_size(account, 100.0, "CRYPTO", "portion", 0.25, None) == (
        2.5,
        250.0,
    )
    assert trade_execution_tool._calc_open_size(account, 100.0, "CRYPTO", "usd", None, 120.0) == (
        1.2,
        120.0,
    )
    assert trade_execution_tool._calc_open_size(account, 100.0, "CRYPTO", "all_in", None, None) == (
        10.0,
        1000.0,
    )

    position = type(
        "Position",
        (),
        {"leverage": 1, "quantity": 2.0, "available_quantity": 2.0},
    )()
    assert trade_execution_tool._calc_close_size(
        position, "CRYPTO", 100.0, "portion", None, None, 0.25
    ) == (0.5, 50.0)
    assert trade_execution_tool._calc_close_size(
        position, "CRYPTO", 100.0, "portion", None, None, 0.5
    ) == (1.0, 100.0)
    assert trade_execution_tool._calc_close_size(
        position, "CRYPTO", 100.0, "close_all", None, None, None
    ) == (2.0, 200.0)


def test_execute_trade_hold_and_close_all_without_positions(monkeypatch):
    account = type("Account", (), {"id": 1})()

    class Query:
        def filter(self, *args):
            return self

        def first(self):
            return account

        def all(self):
            return []

    class DB:
        def query(self, model):
            return Query()

        def commit(self):
            return None

    monkeypatch.setattr(trade_execution_tool, "_save_trade_log", lambda **kwargs: None)
    hold = trade_execution_tool.execute_trade_tool(DB(), 1, "hold", reason="wait")
    assert hold["executed"] is True
    assert hold["operation"] == "hold"

    close_all = trade_execution_tool.execute_trade_tool(DB(), 1, "close_all")
    assert close_all == {
        "executed": True,
        "operation": "close_all",
        "closed_orders": [],
        "message": "No positions to close.",
    }
