"""Grid sell commitments must fit inventory across repricing and legacy cleanup."""

from decimal import Decimal

import pytest

from benchmark.application.trading import CreateOrderCommand, ProcessPendingOrders
from benchmark.contracts import Market
from database.models import Account, Order, Position
from services import baselines, order_matching
from trading import test_gateway_reliability as reliability

session_factory = reliability.session_factory


@pytest.fixture
def grid_environment(session_factory, monkeypatch):
    from benchmark.application import trading

    quote = {"price": 100.0}
    gateway = reliability._gateway(session_factory)
    monkeypatch.setattr(trading, "get_default_trade_gateway", lambda: gateway)
    monkeypatch.setattr(order_matching, "get_last_price", lambda *args: quote["price"])
    monkeypatch.setattr(baselines, "calc_positions_value", lambda db, aid: sum(
        float(p.quantity) * quote["price"] for p in db.query(Position).filter_by(account_id=aid)
    ))
    with session_factory() as db:
        db.add(Position(
            account_id=1, symbol="BTC", name="Bitcoin", market="CRYPTO", side="LONG",
            quantity=1, available_quantity=1, avg_cost=100, leverage=1,
        ))
        db.commit()
    return gateway, quote


def strategy(cap=24, levels=2):
    return baselines.GridBaseline(baselines.GridConfig(
        universe=["BTC"], levels=levels, step_pct=0.01, capital_usage=0.1,
        min_order_usd=1, max_pending_per_symbol=cap, cleanup_band_mult=1.5,
    ))


def maintain(session_factory, grid, price=100):
    with session_factory() as db:
        grid._maintain_symbol_grid(db, db.get(Account, 1), "BTC", "CRYPTO", price)


def inventory_commitment(session_factory):
    with session_factory() as db:
        position = db.query(Position).filter_by(symbol="BTC").one()
        sells = db.query(Order).filter_by(symbol="BTC", side="SELL", status="PENDING").all()
        return position.available_quantity, sum(
            (Decimal(str(order.quantity)) - Decimal(str(order.filled_quantity)) for order in sells), Decimal(0)
        )


def test_repriced_grid_does_not_resell_inventory_committed_to_existing_orders(
    session_factory, grid_environment,
):
    grid = strategy()
    for price in (100, 100.5, 100.25, 100.75):
        maintain(session_factory, grid, price)
        available, committed = inventory_commitment(session_factory)
        assert committed == available == 1
    with session_factory() as db:
        assert db.query(Order).filter_by(side="SELL", status="PENDING").count() == 2


def test_full_legacy_grid_cancels_excess_sells_before_pending_cap(
    session_factory, grid_environment, caplog,
):
    gateway, quote = grid_environment
    ids = []
    for price in ("100.5", "101", "101.5", "102"):
        result = gateway.create_order(CreateOrderCommand(
            account_id=1, symbol="BTC", market=Market.CRYPTO, side="SELL",
            order_type="LIMIT", price=Decimal(price), quantity=Decimal("0.75"),
        ))
        assert result.accepted
        ids.append(result.order_id)
    assert inventory_commitment(session_factory) == (Decimal(1), Decimal(3))
    maintain(session_factory, strategy(cap=4))
    with session_factory() as db:
        assert db.get(Order, ids[0]).status == "PENDING"
        assert all(db.get(Order, oid).status == "CANCELLED" for oid in ids[1:])
        assert db.query(Order).filter_by(status="PENDING").count() <= 4
    available, committed = inventory_commitment(session_factory)
    assert 0 < committed <= available

    quote["price"] = 103
    result = gateway.process_pending(ProcessPendingOrders(account_id=1))
    assert result.executed >= 1
    available, committed = inventory_commitment(session_factory)
    assert committed <= available
    assert "Insufficient position" not in caplog.text


@pytest.mark.parametrize("cap,levels,expected_orders,slice_qty", [
    (3, 3, 3, "0.33333333"), (24, 6, 12, "0.16666666"),
])
def test_grid_rounds_sell_slices_down_and_respects_odd_pending_limit(
    session_factory, grid_environment, cap, levels, expected_orders, slice_qty,
):
    maintain(session_factory, strategy(cap=cap, levels=levels))
    available, committed = inventory_commitment(session_factory)
    assert Decimal(0) < committed <= available
    with session_factory() as db:
        pending = db.query(Order).filter_by(status="PENDING").all()
        assert len(pending) == expected_orders
        assert {Decimal(str(o.quantity)) for o in pending if o.side == "SELL"} == {Decimal(slice_qty)}
