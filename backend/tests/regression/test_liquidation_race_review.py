"""Liquidation must revalidate account risk after acquiring its write lock."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.parametrize("recover", [False, True])
def test_margin_recovery_before_gateway_lock_prevents_stale_liquidation(tmp_path, monkeypatch, recover):
    from benchmark.application import trading
    from benchmark.persistence import SqlAlchemyUnitOfWork
    from database.connection import Base
    from database.models import Account, Position, Trade, User
    from services import market_data, order_executor_leverage, scheduler
    from services.agent import trade_execution_tool

    engine = create_engine(f"sqlite:///{tmp_path / 'liquidation-race.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    try:
        with sessions() as db:
            db.add(User(id=1, username="liquidation-review"))
            db.flush()
            db.add(Account(
                id=1, user_id=1, name="liquidation-review", initial_capital=50,
                current_cash=0, frozen_cash=0, margin_used=50,
                maintenance_margin_ratio=Decimal("0.5"),
            ))
            db.flush()
            db.add(Position(
                account_id=1, symbol="BTC", name="Bitcoin", market="CRYPTO",
                quantity=1, available_quantity=1, avg_cost=100,
                leverage=2, side="LONG",
            ))
            db.commit()

        monkeypatch.setattr(scheduler, "shutdown_cancellation_requested", lambda: False)
        for module in (market_data, order_executor_leverage, trade_execution_tool):
            monkeypatch.setattr(module, "get_last_price", lambda *args: 40)
        monkeypatch.setattr(market_data, "get_trading_price", lambda *args: 40)
        monkeypatch.setattr(trade_execution_tool, "calc_positions_value", lambda *args: 0)

        gateway = trading.SynchronousTradeCommandGateway(
            lambda: SqlAlchemyUnitOfWork(sessions)
        )
        recovered = []

        def recover_before_execution():
            # The caller holds an account snapshot from before recovery. Another
            # transaction restores solvency before the gateway locks the account.
            if recover:
                with sessions() as db:
                    db.get(Account, 1).current_cash = 10000
                    db.commit()
            recovered.append(True)
            return gateway

        monkeypatch.setattr(trading, "get_default_trade_gateway", recover_before_execution)
        monitor = object.__new__(scheduler.TaskScheduler)
        with sessions() as db:
            monitor._check_account_margin(db, db.get(Account, 1))

        assert recovered, "The test must reach the independent gateway transaction"
        with sessions() as db:
            assert db.query(Trade).count() == (0 if recover else 1)
            assert db.query(Position).one().quantity == Decimal("1" if recover else "0")
            if recover:
                assert db.get(Account, 1).current_cash == Decimal("10000")
    finally:
        engine.dispose()
