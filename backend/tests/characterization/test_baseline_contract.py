from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from database.models import Order, Position
from services import baselines


def _db_without_positions():
    def query(model):
        result = MagicMock()
        filtered = result.filter.return_value
        filtered.all.return_value = []
        filtered.first.return_value = None
        filtered.order_by.return_value.all.return_value = []
        if model not in {Order, Position}:
            raise AssertionError(f"unexpected baseline query: {model}")
        return result

    db = MagicMock()
    db.query.side_effect = query
    return db


def test_buy_hold_baseline_places_market_buy_without_live_market(monkeypatch):
    account = SimpleNamespace(
        id=101,
        name="buy_hold",
        agent_type="buy_hold",
        current_cash=10_000.0,
        frozen_cash=0.0,
    )
    orders = []

    def create_order(**kwargs):
        orders.append(kwargs)
        return SimpleNamespace(id=1, price=50_000.0, filled_quantity=0.01)

    monkeypatch.setattr(baselines, "_is_trading_open", lambda *args: True)
    monkeypatch.setattr(baselines, "calc_positions_value", lambda *args: 0.0)
    monkeypatch.setattr(baselines, "create_order", create_order)
    monkeypatch.setattr(baselines, "check_and_execute_order", lambda *args: True)
    baseline = baselines.BuyHoldBaseline(
        baselines.BuyHoldConfig(
            universe=["BTC"],
            rebalance_seconds=60,
            capital_usage=0.95,
            min_trade_usd=5.0,
        )
    )

    baseline.run_tick(
        _db_without_positions(),
        account,
        {"BTC": 50_000.0},
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert len(orders) == 1
    assert orders[0]["symbol"] == "BTC"
    assert orders[0]["side"] == "BUY"
    assert orders[0]["market"] == "CRYPTO"
    assert orders[0]["order_type"] == "MARKET"


def test_grid_baseline_places_limit_orders_without_live_market(monkeypatch):
    account = SimpleNamespace(
        id=102,
        name="grid",
        agent_type="grid",
        current_cash=10_000.0,
        frozen_cash=0.0,
    )
    orders = []

    def create_order(**kwargs):
        orders.append(kwargs)
        return SimpleNamespace(id=len(orders), price=kwargs["price"], filled_quantity=0.0)

    monkeypatch.setattr(baselines, "_is_trading_open", lambda *args: True)
    monkeypatch.setattr(baselines, "calc_positions_value", lambda *args: 0.0)
    monkeypatch.setattr(baselines, "create_order", create_order)
    monkeypatch.setattr(baselines, "check_and_execute_order", lambda *args: False)
    monkeypatch.setattr(baselines, "process_all_pending_orders", lambda *args: (0, 0))
    baseline = baselines.GridBaseline(
        baselines.GridConfig(
            universe=["BTC"],
            levels=2,
            step_pct=0.01,
            capital_usage=0.8,
            min_order_usd=5.0,
            max_pending_per_symbol=24,
            cleanup_band_mult=1.5,
        )
    )

    baseline.run_tick(_db_without_positions(), account, {"BTC": 50_000.0})

    assert orders
    limit_orders = [order for order in orders if order["order_type"] == "LIMIT"]
    assert limit_orders
    assert {order["side"] for order in limit_orders}.issubset({"BUY", "SELL"})
