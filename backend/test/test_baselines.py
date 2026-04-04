"""Unit tests for services.baselines (buy_hold / grid helpers and tick logic)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.baselines import (
    BuyHoldBaseline,
    BuyHoldConfig,
    GridBaseline,
    GridConfig,
    _align_period_end,
    _infer_market,
    get_buy_hold_config,
    get_grid_config,
    is_baseline_trading_account,
)
from database.models import Order, Position


def test_is_baseline_trading_account_by_agent_type():
    assert is_baseline_trading_account(SimpleNamespace(agent_type="buy_hold", name="x")) is True
    assert is_baseline_trading_account(SimpleNamespace(agent_type="GRID", name="x")) is True
    assert is_baseline_trading_account(SimpleNamespace(agent_type=" react ", name="x")) is False


def test_is_baseline_trading_account_by_name_when_agent_type_wrong():
    assert is_baseline_trading_account(SimpleNamespace(agent_type="react", name="buy_hold")) is True
    assert is_baseline_trading_account(SimpleNamespace(agent_type="react", name="grid")) is True
    assert is_baseline_trading_account(SimpleNamespace(agent_type=None, name=" Grid ")) is True
    assert is_baseline_trading_account(SimpleNamespace(agent_type="react", name="gpt-trader")) is False


def test_align_period_end():
    now = datetime(2026, 4, 2, 12, 34, 56, tzinfo=timezone.utc)
    end = _align_period_end(now, interval_seconds=3600)
    assert end <= now
    assert end.minute == 0 and end.second == 0
    assert (int(now.timestamp()) - int(end.timestamp())) < 3600


def test_align_period_end_invalid():
    with pytest.raises(ValueError):
        _align_period_end(datetime.now(timezone.utc), 0)


def test_infer_market_crypto_vs_us():
    from services.trading_symbols import AI_TRADING_SYMBOLS
    from services.alpaca_market_data import SUPPORTED_STOCKS

    crypto = AI_TRADING_SYMBOLS[0]
    assert _infer_market(crypto) == "CRYPTO"
    assert _infer_market(crypto.lower()) == "CRYPTO"
    if SUPPORTED_STOCKS:
        us_sym = SUPPORTED_STOCKS[0]
        assert _infer_market(us_sym) == "US"


def _db_mock_for_positions(positions: list):
    """Minimal session mock: query(Position) returns positions; query(Order) returns []."""

    def query_side_effect(model):
        q = MagicMock()
        filt = MagicMock()
        q.filter.return_value = filt
        filt.all.return_value = []
        filt.first.return_value = None
        ob = MagicMock()
        filt.order_by.return_value = ob
        ob.all.return_value = []
        if model is Position:
            filt.all.return_value = positions
            filt.first.return_value = None
        elif model is Order:
            filt.order_by.return_value.all.return_value = []
        return q

    db = MagicMock()
    db.query.side_effect = query_side_effect
    return db


@pytest.fixture
def sample_account():
    return SimpleNamespace(
        id=101,
        name="buy_hold",
        agent_type="buy_hold",
        current_cash=10_000.0,
        frozen_cash=0.0,
    )


@patch("services.baselines._is_trading_open", return_value=True)
@patch("services.baselines.check_and_execute_order", return_value=True)
@patch("services.baselines.create_order")
@patch("services.baselines.calc_positions_value", return_value=0.0)
def test_buy_hold_run_tick_places_market_buy(
    _calc_pv, mock_create_order, _chk, _open, sample_account
):
    mock_order = SimpleNamespace(id=1, price=50000.0, filled_quantity=0.01)
    mock_create_order.return_value = mock_order

    cfg = BuyHoldConfig(
        universe=["BTC"],
        rebalance_seconds=60,
        capital_usage=0.95,
        min_trade_usd=5.0,
    )
    bh = BuyHoldBaseline(config=cfg)
    db = _db_mock_for_positions([])
    prices = {"BTC": 50_000.0}
    now = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)

    bh.run_tick(db, sample_account, prices, now=now)

    assert mock_create_order.called
    call_kw = mock_create_order.call_args.kwargs
    assert call_kw["symbol"] == "BTC"
    assert call_kw["side"] == "BUY"
    assert call_kw["market"] == "CRYPTO"
    assert call_kw["order_type"] == "MARKET"


@patch("services.baselines._is_trading_open", return_value=True)
@patch("services.baselines.check_and_execute_order", return_value=True)
@patch("services.baselines.create_order")
@patch("services.baselines.calc_positions_value", return_value=0.0)
def test_buy_hold_skips_second_tick_same_period(
    _calc_pv, mock_create_order, _chk, _open, sample_account
):
    mock_create_order.return_value = SimpleNamespace(id=1, price=1.0, filled_quantity=1.0)

    cfg = BuyHoldConfig(
        universe=["BTC"],
        rebalance_seconds=3600,
        capital_usage=0.95,
        min_trade_usd=5.0,
    )
    bh = BuyHoldBaseline(config=cfg)
    db = _db_mock_for_positions([])
    prices = {"BTC": 50_000.0}
    t0 = datetime(2026, 4, 2, 10, 15, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 4, 2, 10, 20, 0, tzinfo=timezone.utc)

    bh.run_tick(db, sample_account, prices, now=t0)
    assert mock_create_order.call_count == 1
    mock_create_order.reset_mock()
    bh.run_tick(db, sample_account, prices, now=t1)
    assert mock_create_order.call_count == 0


@patch("services.baselines._is_trading_open", return_value=True)
@patch("services.baselines.calc_positions_value", return_value=0.0)
def test_buy_hold_no_op_when_no_prices(_calc_pv, _open, sample_account):
    cfg = BuyHoldConfig(
        universe=["BTC"],
        rebalance_seconds=60,
        capital_usage=0.95,
        min_trade_usd=5.0,
    )
    bh = BuyHoldBaseline(config=cfg)
    db = _db_mock_for_positions([])
    with patch("services.baselines.create_order") as mock_co:
        bh.run_tick(db, sample_account, {}, now=datetime.now(timezone.utc))
        assert not mock_co.called


@patch("services.baselines._is_trading_open", return_value=True)
@patch("services.baselines.check_and_execute_order", return_value=True)
@patch("services.baselines.create_order")
@patch("services.baselines.calc_positions_value", return_value=0.0)
@patch("services.baselines.process_all_pending_orders")
def test_grid_run_tick_creates_limit_orders(
    _proc, _calc_pv, mock_create_order, _chk, _open, sample_account
):
    mock_create_order.return_value = SimpleNamespace(id=2, price=100.0, filled_quantity=0.0)

    cfg = GridConfig(
        universe=["BTC"],
        levels=2,
        step_pct=0.01,
        capital_usage=0.8,
        min_order_usd=5.0,
        max_pending_per_symbol=24,
        cleanup_band_mult=1.5,
    )
    grid = GridBaseline(config=cfg)
    db = _db_mock_for_positions([])
    sample_account.name = "grid"
    sample_account.agent_type = "grid"

    grid.run_tick(db, sample_account, {"BTC": 50_000.0})

    limit_calls = [
        c for c in mock_create_order.call_args_list if c.kwargs.get("order_type") == "LIMIT"
    ]
    assert len(limit_calls) >= 1
    sides = {c.kwargs["side"] for c in limit_calls}
    assert "BUY" in sides or "SELL" in sides


def test_get_buy_hold_config_shape():
    c = get_buy_hold_config()
    assert c.universe
    assert c.rebalance_seconds >= 60
    assert 0.0 <= c.capital_usage <= 1.0


def test_get_grid_config_shape():
    c = get_grid_config()
    assert c.universe
    assert c.levels >= 1
    assert c.step_pct > 0
