from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from benchmark.application.trading import SynchronousTradeCommandGateway
from benchmark.contracts import Market, TradeCommand
from benchmark.persistence import SqlAlchemyUnitOfWork
from database.models import Account, TradeCommandReceipt, User


pytestmark = pytest.mark.integration


def _mysql_url() -> str:
    value = os.getenv("MYSQL_TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("MYSQL_TEST_DATABASE_URL is required for MySQL row-lock tests")
    if not value.startswith("mysql"):
        pytest.fail("MYSQL_TEST_DATABASE_URL must point to a dedicated MySQL database")
    return value


def test_different_idempotency_keys_are_serialized_by_account_row_lock():
    engine = create_engine(_mysql_url(), pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    suffix = uuid4().hex[:12]
    account_id = 800_000_000 + int(suffix[:6], 16) % 100_000_000
    username = f"gateway-lock-{suffix}"

    with factory.begin() as session:
        user = User(username=username, is_active="true")
        session.add(user)
        session.flush()
        session.add(
            Account(
                id=account_id,
                user_id=user.id,
                version="v1",
                name=username,
                account_type="AI",
                initial_capital=10000,
                current_cash=10000,
                frozen_cash=0,
                margin_used=0,
                is_active="true",
            )
        )
        user_id = user.id

    first_entered = Event()
    second_entered = Event()
    release_first = Event()
    call_count = 0
    count_lock = Lock()

    def executor(session, command):
        nonlocal call_count
        with count_lock:
            call_count += 1
            ordinal = call_count
        if ordinal == 1:
            first_entered.set()
            assert release_first.wait(timeout=10)
        else:
            second_entered.set()
        account = session.query(Account).filter(Account.id == account_id).one()
        account.current_cash = Decimal(str(account.current_cash)) - Decimal("1000")
        return {"executed": True, "order_id": ordinal}

    def command(key: str) -> TradeCommand:
        return TradeCommand(
            account_id=account_id,
            operation="open",
            market=Market.CRYPTO,
            symbol="BTC",
            direction="long",
            sizing_mode="usd",
            sizing_value=Decimal("1000"),
            leverage=1,
            reason="mysql row-lock test",
            idempotency_key=key,
        )

    try:
        first_gateway = SynchronousTradeCommandGateway(
            lambda: SqlAlchemyUnitOfWork(factory),
            executor=lambda transaction, trade_command: (
                transaction.run_legacy_executor(executor, trade_command)
            ),
        )
        second_gateway = SynchronousTradeCommandGateway(
            lambda: SqlAlchemyUnitOfWork(factory),
            executor=lambda transaction, trade_command: (
                transaction.run_legacy_executor(executor, trade_command)
            ),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_gateway.execute, command(f"{suffix}:first"))
            assert first_entered.wait(timeout=10)
            second = pool.submit(second_gateway.execute, command(f"{suffix}:second"))
            assert not second_entered.wait(timeout=0.5)
            release_first.set()
            assert first.result(timeout=15).accepted is True
            assert second.result(timeout=15).accepted is True

        with factory() as session:
            account = session.query(Account).filter(Account.id == account_id).one()
            assert Decimal(str(account.current_cash)) == Decimal("8000")
            assert (
                session.query(TradeCommandReceipt)
                .filter(TradeCommandReceipt.account_id == account_id)
                .count()
                == 2
            )
    finally:
        release_first.set()
        with factory.begin() as session:
            session.query(TradeCommandReceipt).filter(
                TradeCommandReceipt.account_id == account_id
            ).delete(synchronize_session=False)
            session.query(Account).filter(Account.id == account_id).delete(
                synchronize_session=False
            )
            session.query(User).filter(User.id == user_id).delete(
                synchronize_session=False
            )
        engine.dispose()
