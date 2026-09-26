"""Independent financial invariants for manual and Agent margin orders."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from benchmark.application.trading import CreateOrderCommand, ProcessPendingOrders
from benchmark.contracts import Market
from database.models import Account, Order, Position, Trade
from trading import test_gateway_reliability as reliability
from trading.test_gateway_reliability import _command, _gateway

session_factory = reliability.session_factory


@pytest.fixture
def margin_gateway(session_factory, monkeypatch):
    from services import asset_calculator, market_data, order_executor_leverage, order_matching
    from services.agent import trade_execution_tool

    for module in (asset_calculator, market_data, order_executor_leverage, order_matching, trade_execution_tool):
        monkeypatch.setattr(module, "get_last_price", lambda *args, **kwargs: 100.0)
    monkeypatch.setattr(market_data, "get_trading_price", lambda *args, **kwargs: 100.0)
    monkeypatch.setattr(trade_execution_tool, "calc_positions_value", lambda *args: 0.0)
    monkeypatch.setattr(order_executor_leverage, "now_utc", lambda: datetime(2026, 9, 6, tzinfo=timezone.utc))
    return _gateway(session_factory)


def manual(side="BUY", quantity="1", leverage=1):
    return CreateOrderCommand(
        account_id=1, symbol="BTC", market=Market.CRYPTO, side=side,
        order_type="MARKET", quantity=Decimal(quantity), leverage=leverage,
    )


@pytest.mark.parametrize("leverage", [2, 50])
def test_manual_margin_open_and_partial_full_close_balance_collateral(
    session_factory, margin_gateway, leverage,
):
    opened = margin_gateway.create_order(manual(leverage=leverage))
    assert opened.accepted
    with session_factory() as db:
        account, position = db.get(Account, 1), db.query(Position).one()
        assert account.margin_used == Decimal(100) / leverage
        assert account.current_cash == Decimal(10000) - Decimal(100) / leverage - Decimal("0.07")
        assert position.quantity == position.available_quantity == 1
        assert position.leverage == leverage
        assert position.side == "LONG"
        assert db.query(Order).count() == db.query(Trade).count() == 1
        assert db.query(Trade).one().order_id == opened.order_id
        assert db.query(Trade).one().side == "BUY"

    # A manual SELL defaults to leverage=1; settlement must use the position's
    # actual leverage and release only the collateral belonging to this fill.
    first_close = margin_gateway.create_order(manual("SELL", "0.4"))
    assert first_close.accepted
    with session_factory() as db:
        account, position = db.get(Account, 1), db.query(Position).one()
        assert account.margin_used == Decimal(60) / leverage
        assert account.current_cash == (Decimal(10000) - Decimal(60) / leverage - Decimal("0.098")).quantize(Decimal("0.01"))
        assert position.quantity == position.available_quantity == Decimal("0.6")
        assert db.query(Trade).filter(Trade.order_id == first_close.order_id).count() == 1

    assert margin_gateway.create_order(manual("SELL", "0.6")).accepted
    with session_factory() as db:
        account, position = db.get(Account, 1), db.query(Position).one()
        assert account.margin_used == 0
        assert account.current_cash == Decimal("9999.86")
        assert position.quantity == position.available_quantity == 0
        assert position.side is None and position.leverage == 1
        assert db.query(Order).count() == db.query(Trade).count() == 3
        assert all(order.status == "FILLED" for order in db.query(Order))


@pytest.mark.parametrize("original_leverage,new_leverage", [(1, 2), (2, 1), (2, 5)])
def test_manual_mixed_leverage_buy_rejected_without_financial_changes(
    session_factory, margin_gateway, original_leverage, new_leverage,
):
    assert margin_gateway.create_order(manual(leverage=original_leverage)).accepted
    with session_factory() as db:
        account, position = db.get(Account, 1), db.query(Position).one()
        original = (account.current_cash, account.margin_used, position.quantity, position.avg_cost, position.leverage)
    rejected = margin_gateway.create_order(manual(leverage=new_leverage))
    assert not rejected.accepted
    with session_factory() as db:
        account, position = db.get(Account, 1), db.query(Position).one()
        assert (account.current_cash, account.margin_used, position.quantity, position.avg_cost, position.leverage) == original
        assert db.query(Order).count() == db.query(Trade).count() == 1


@pytest.mark.parametrize("manual_opens", [False, True])
def test_manual_and_agent_share_margin_and_interest_settlement(
    session_factory, margin_gateway, manual_opens,
):
    if manual_opens:
        opened = margin_gateway.create_order(manual(leverage=5))
    else:
        opened = margin_gateway.execute(_command(
            key="cross-path-open", leverage=5, sizing_mode="usd", sizing_value=Decimal("100"),
        ))
    assert opened.accepted
    with session_factory() as db:
        position = db.query(Position).one()
        position.last_interest_time = datetime(2026, 9, 6, tzinfo=timezone.utc) - timedelta(hours=24)
        db.commit()
    if manual_opens:
        closed = margin_gateway.execute(_command(
            key="cross-path-close", operation="close", direction="long",
            sizing_mode="close_ratio", sizing_value=Decimal("1"),
        ))
    else:
        closed = margin_gateway.create_order(manual("SELL"))
    assert closed.accepted
    with session_factory() as db:
        account, position = db.get(Account, 1), db.query(Position).one()
        # $80 borrowed for 24h; both entry points must settle the same debt.
        assert position.accumulated_interest == Decimal("0.024")
        assert account.current_cash == Decimal("9999.84")
        assert account.margin_used == 0
        assert position.quantity == position.available_quantity == 0
        assert position.side is None and position.leverage == 1
        assert db.query(Order).count() == db.query(Trade).count() == 2
        assert db.query(Trade).filter(Trade.order_id == closed.order_id).count() == 1


def test_unfunded_margin_pending_order_does_not_block_other_valid_fill(
    session_factory, margin_gateway,
):
    assert margin_gateway.create_order(CreateOrderCommand(
        account_id=1, symbol="ETH", market=Market.CRYPTO, side="BUY",
        order_type="MARKET", quantity=Decimal("1"),
    )).accepted
    pending_buy = margin_gateway.create_order(CreateOrderCommand(
        account_id=1, symbol="BTC", market=Market.CRYPTO, side="BUY",
        order_type="LIMIT", quantity=Decimal("1"), price=Decimal("110"), leverage=2,
    ))
    pending_sell = margin_gateway.create_order(CreateOrderCommand(
        account_id=1, symbol="ETH", market=Market.CRYPTO, side="SELL",
        order_type="LIMIT", quantity=Decimal("1"), price=Decimal("90"),
    ))
    assert pending_buy.accepted and pending_sell.accepted
    with session_factory() as db:
        db.get(Account, 1).current_cash = 0
        db.commit()

    processed = margin_gateway.process_pending(ProcessPendingOrders(account_id=1))
    assert processed.processed == 2 and processed.executed == 1
    with session_factory() as db:
        assert db.get(Order, pending_buy.order_id).status == "PENDING"
        assert db.get(Order, pending_sell.order_id).status == "FILLED"
        assert db.query(Trade).filter(Trade.order_id == pending_buy.order_id).count() == 0
        assert db.query(Trade).filter(Trade.order_id == pending_sell.order_id).count() == 1
        assert db.get(Account, 1).margin_used == 0


def test_oversized_margin_pending_close_rolls_back_interest_and_continues(
    session_factory, margin_gateway,
):
    assert margin_gateway.create_order(manual(leverage=2)).accepted
    pending = margin_gateway.create_order(CreateOrderCommand(
        account_id=1, symbol="BTC", market=Market.CRYPTO, side="SELL",
        order_type="LIMIT", quantity=Decimal("1"), price=Decimal("90"),
    ))
    assert pending.accepted
    assert margin_gateway.create_order(manual("SELL", "0.5")).accepted
    other = margin_gateway.create_order(CreateOrderCommand(
        account_id=1, symbol="ETH", market=Market.CRYPTO, side="BUY",
        order_type="LIMIT", quantity=Decimal("1"), price=Decimal("110"),
    ))
    assert other.accepted
    with session_factory() as db:
        position = db.query(Position).filter(Position.symbol == "BTC").one()
        position.last_interest_time = datetime(2026, 9, 6, tzinfo=timezone.utc) - timedelta(hours=24)
        expected_cash = db.get(Account, 1).current_cash - Decimal("100.10")
        db.commit()

    result = margin_gateway.process_pending(ProcessPendingOrders(account_id=1))
    assert result.processed == 2 and result.executed == 1
    with session_factory() as db:
        position = db.query(Position).filter(Position.symbol == "BTC").one()
        assert position.quantity == Decimal("0.5")
        assert position.accumulated_interest == 0
        assert db.get(Account, 1).current_cash == expected_cash
        assert db.get(Account, 1).margin_used == Decimal("25")
        assert db.get(Order, pending.order_id).status == "PENDING"
        assert db.get(Order, other.order_id).status == "FILLED"


@pytest.mark.parametrize('size_mode,amount', [('portion',0.15),('usd',1500)])
def test_open_ignores_irrelevant_close_ratio_from_tool_arguments(session_factory, margin_gateway, size_mode, amount):
    from services.agent.trade_execution_tool import execute_trade_tool
    result=execute_trade_tool(
        db=None,account_id=1,operation='open',symbol='BTC',market='CRYPTO',
        direction='long',size_mode=size_mode,
        target_portion_of_balance=amount if size_mode=='portion' else 0,
        usd_amount=amount if size_mode=='usd' else 0,
        close_ratio=0,leverage=2,decision_round_id='open-with-close-default',
        tool_call_id='trade1',gateway=margin_gateway,
    )
    assert result['executed'] is True
    with session_factory() as db:
        assert db.query(Order).count()==db.query(Trade).count()==1
        assert db.query(Position).one().quantity > 0
