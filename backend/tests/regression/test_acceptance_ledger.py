"""The live verifier must reject fabricated success and corrupted balances."""

from decimal import Decimal as D
from types import SimpleNamespace as Row

import pytest

from scripts.live_agent_check import verify_ledger


def test_verifier_rejects_fabricated_trade_result():
    account = Row(id=1, initial_capital=D(10000), current_cash=D(1), margin_used=D(999))
    with pytest.raises(AssertionError, match="Agent result/fill mismatch"):
        verify_ledger(
            account,
            [],
            [],
            [],
            [],
            {"executed_trades": [{"executed": True, "order_id": 999, "trade_id": 999}]},
        )


def test_verifier_reconciles_real_fill_and_rejects_corrupted_cash():
    import json

    account = Row(
        id=1, initial_capital=D(10000), current_cash=D("9799.3"), margin_used=D(200)
    )
    order = Row(id=2, leverage=5, side="LONG", filled_quantity=D(10))
    trade = Row(
        id=3,
        order_id=2,
        market="CRYPTO",
        symbol="BTC",
        quantity=D(10),
        price=D(100),
        taker_fee=D(".7"),
        commission=D(".7"),
        interest_charged=D(0),
    )
    position = Row(
        market="CRYPTO",
        symbol="BTC",
        quantity=D(10),
        available_quantity=D(10),
        avg_cost=D(100),
        side="LONG",
        leverage=5,
    )
    receipt = Row(
        result_json=json.dumps(
            {
                "normalized_command": {"account_id": 1},
                "executed": True,
                "order_id": 2,
                "trade_id": 3,
                "raw_result": {},
            }
        )
    )
    result = {"executed_trades": [{"executed": True, "order_id": 2, "trade_id": None}]}
    verify_ledger(account, [order], [trade], [position], [receipt], result)
    account.current_cash += 1
    with pytest.raises(AssertionError, match="Cash replay mismatch"):
        verify_ledger(account, [order], [trade], [position], [receipt], result)


def test_verifier_reconciles_us_short_and_partial_cover():
    import json

    account = Row(
        id=1, initial_capital=D(10000), current_cash=D("10209.60"), margin_used=D(0)
    )
    orders = [
        Row(id=2, leverage=1, side="SELL", filled_quantity=3.0),
        Row(id=4, leverage=1, side="BUY", filled_quantity=1.0),
    ]
    trades = [
        Row(
            id=3,
            order_id=2,
            market="US",
            symbol="META",
            quantity=D(3),
            price=D(100),
            commission=D(".3"),
            taker_fee=D(0),
            interest_charged=D(0),
        ),
        Row(
            id=5,
            order_id=4,
            market="US",
            symbol="META",
            quantity=D(1),
            price=D(90),
            commission=D(".1"),
            taker_fee=D(0),
            interest_charged=D(0),
        ),
    ]
    positions = [
        Row(
            market="US",
            symbol="META",
            quantity=D(2),
            available_quantity=D(2),
            avg_cost=D(100),
            side="SHORT",
            leverage=1,
        )
    ]
    receipts = [
        Row(
            result_json=json.dumps(
                {
                    "normalized_command": {"account_id": 1},
                    "executed": True,
                    "order_id": row.id,
                    "trade_id": None,
                    "raw_result": {},
                }
            )
        )
        for row in orders
    ]
    result = {
        "executed_trades": [
            {"executed": True, "order_id": row.id, "trade_id": None} for row in orders
        ]
    }
    verify_ledger(account, orders, trades, positions, receipts, result)


def test_verifier_accepts_mysql_float_text_precision_from_real_fill():
    import json

    account = Row(
        id=2,
        initial_capital=D("10000"),
        current_cash=D("8748.25"),
        margin_used=D("1250"),
    )
    order = Row(id=1, side="LONG", leverage=2, filled_quantity=21.4694)
    trade = Row(
        id=1,
        order_id=1,
        market="CRYPTO",
        symbol="SOL",
        quantity=D("21.46936300"),
        price=D("116.445000"),
        taker_fee=D("1.750000"),
        commission=D("1.750000"),
        interest_charged=D(0),
    )
    position = Row(
        market="CRYPTO",
        symbol="SOL",
        quantity=trade.quantity,
        available_quantity=trade.quantity,
        avg_cost=trade.price,
        side="LONG",
        leverage=2,
    )
    receipt = Row(
        result_json=json.dumps(
            {
                "normalized_command": {"account_id": 2},
                "executed": True,
                "order_id": 1,
                "trade_id": None,
                "raw_result": {},
            }
        )
    )
    result = {"executed_trades": [{"executed": True, "order_id": 1, "trade_id": None}]}
    verify_ledger(account, [order], [trade], [position], [receipt], result)
    order.filled_quantity = 21.47
    with pytest.raises(AssertionError, match="Order FLOAT"):
        verify_ledger(account, [order], [trade], [position], [receipt], result)
