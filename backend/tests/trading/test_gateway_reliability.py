from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, Lock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from benchmark.application.trading import (
    CancelOrderCommand,
    CreateOrderCommand,
    ProcessPendingOrders,
    SynchronousTradeCommandGateway,
)
from benchmark.contracts import Market, TradeCommand
from benchmark.contracts import TradeCommandResult
from benchmark.contracts.errors import TradeGatewayError
from benchmark.persistence import SqlAlchemyUnitOfWork
from benchmark.persistence.trade_transactions import TradeTransactionOperations
from database.connection import Base
from database.models import (
    AIDecisionLog,
    Account,
    Order,
    Position,
    Trade,
    TradeCommandReceipt,
    User,
)


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gateway.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = factory()
    user = User(username="trader", is_active="true")
    session.add(user)
    session.flush()
    session.add(
        Account(
            id=1,
            user_id=user.id,
            version="v1",
            name="gateway-account",
            account_type="AI",
            initial_capital=10000,
            current_cash=10000,
            frozen_cash=0,
            margin_used=0,
            is_active="true",
        )
    )
    session.commit()
    session.close()
    yield factory
    engine.dispose()


def _test_uow_factory(session_factory, executor):
    class TestUnitOfWork(SqlAlchemyUnitOfWork):
        def _build_adapters(self):
            super()._build_adapters()
            operations = self.trade_operations

            class TestTradeOperations:
                def execute_trade(self, command):
                    return executor(operations._session_provider(), command)

                def __getattr__(self, name):
                    return getattr(operations, name)

            self.trade_operations = TestTradeOperations()

    return lambda: TestUnitOfWork(session_factory)


def _gateway(session_factory, executor=None):
    if executor is None:
        uow_factory = lambda: SqlAlchemyUnitOfWork(session_factory)
    else:
        uow_factory = _test_uow_factory(session_factory, executor)
    return SynchronousTradeCommandGateway(
        uow_factory,
    )


def _command(key="round-1:call-1", **changes):
    values = {
        "account_id": 1,
        "operation": "open",
        "market": Market.CRYPTO,
        "symbol": "btc",
        "direction": "long",
        "sizing_mode": "portion",
        "sizing_value": Decimal("0.2"),
        "leverage": 1,
        "reason": "test",
        "idempotency_key": key,
    }
    values.update(changes)
    return TradeCommand(**values)


@dataclass
class _FakeReceipt:
    account_id: int
    idempotency_key: str
    command_json: str
    status: str = "PENDING"
    result_json: str | None = None
    completed_at: object | None = None


class _FakeSavepoint:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


class _FakeTradeOperations:
    def __init__(self):
        self.savepoints = []

    @contextmanager
    def savepoint(self):
        savepoint = _FakeSavepoint()
        self.savepoints.append(savepoint)
        yield savepoint

    def execute_trade(self, command):
        return {"executed": True, "order_id": 7}


class _FakeAccounts:
    def get_for_update(self, account_id):
        return object() if account_id == 1 else None


class _FakeReceipts:
    def __init__(self, store):
        self.store = store

    def get(self, account_id, key):
        return self.store.get((account_id, key))

    def claim(self, account_id, key, command_json):
        receipt = _FakeReceipt(account_id, key, command_json)
        self.store[(account_id, key)] = receipt
        return receipt

    def complete(self, receipt, result_json, completed_at):
        receipt.status = "COMPLETED"
        receipt.result_json = result_json
        receipt.completed_at = completed_at
        return receipt


class _FakeUnitOfWork:
    def __init__(self, store):
        self.accounts = _FakeAccounts()
        self.trade_command_receipts = _FakeReceipts(store)
        self.trade_operations = _FakeTradeOperations()
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None or not self.committed:
            self.rolled_back = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_gateway_application_port_runs_with_fake_uow_without_sqlalchemy():
    receipts = {}
    units = []

    def factory():
        unit = _FakeUnitOfWork(receipts)
        units.append(unit)
        return unit

    result = SynchronousTradeCommandGateway(factory).execute(
        _command(key="fake-port")
    )

    assert result.accepted is True
    assert result.order_id == 7
    assert units[0].committed is True
    assert receipts[(1, "fake-port")].status == "COMPLETED"


def test_application_transaction_port_cannot_supply_a_session_callback():
    assert "run_legacy_executor" not in TradeTransactionOperations.__dict__
    assert set(TradeTransactionOperations.__dict__) >= {
        "execute_trade",
        "savepoint",
        "create_order",
        "cancel_order",
        "execute_order",
    }


def test_success_commits_business_write_and_receipt_once(session_factory):
    calls = []

    def executor(session, command):
        calls.append(command)
        account = session.query(Account).filter(Account.id == 1).one()
        account.current_cash = 9000
        return {"executed": True, "order_id": 10}

    first = _gateway(session_factory, executor).execute(_command())
    second = _gateway(session_factory, executor).execute(_command())

    assert first == second
    assert len(calls) == 1
    with session_factory() as session:
        assert float(session.query(Account).filter(Account.id == 1).one().current_cash) == 9000
        receipt = session.query(TradeCommandReceipt).one()
        assert receipt.status == "COMPLETED"


def test_account_is_locked_before_trade_executor_runs(session_factory, monkeypatch):
    from benchmark.persistence.sqlalchemy_repositories import (
        SqlAlchemyAccountRepository,
    )

    events = []
    original = SqlAlchemyAccountRepository.get_for_update

    def locked(repository, account_id):
        events.append("account_locked")
        return original(repository, account_id)

    def executor(session, command):
        events.append("executor_started")
        return {"executed": True, "order_id": 10}

    monkeypatch.setattr(SqlAlchemyAccountRepository, "get_for_update", locked)

    result = _gateway(session_factory, executor).execute(_command())

    assert result.accepted is True
    assert events == ["account_locked", "executor_started"]


def test_missing_account_is_rejected_before_receipt_claim(session_factory):
    calls = []

    result = _gateway(
        session_factory,
        lambda session, command: calls.append(command) or {"executed": True},
    ).execute(_command(account_id=999, key="missing-account"))

    assert result.accepted is False
    assert result.reject_code == "ACCOUNT_NOT_FOUND"
    assert calls == []
    with session_factory() as session:
        assert session.query(TradeCommandReceipt).count() == 0


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_finite_sizing_is_rejected_before_executor(session_factory, value):
    calls = []
    gateway = _gateway(
        session_factory,
        lambda session, command: calls.append(command) or {"executed": True},
    )

    with pytest.raises(TradeGatewayError) as caught:
        gateway.execute(
            _command(
                key=f"non-finite:{value}",
                sizing_mode="usd",
                sizing_value=Decimal(str(json.loads(value))),
            )
        )

    assert caught.value.code == "SIZING_VALUE_INVALID"
    assert calls == []
    with session_factory() as session:
        account = session.query(Account).filter(Account.id == 1).one()
        assert float(account.current_cash) == 10000
        assert session.query(Order).count() == 0
        assert session.query(Trade).count() == 0
        assert session.query(Position).count() == 0


@pytest.mark.parametrize("field", ["quantity", "price"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_internal_order_command_rejects_non_finite_values(field, value):
    kwargs = {
        "account_id": 1,
        "symbol": "BTC",
        "market": Market.CRYPTO,
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": Decimal("1"),
        "price": Decimal("100"),
    }
    kwargs[field] = Decimal(value)

    with pytest.raises(TradeGatewayError) as caught:
        CreateOrderCommand(**kwargs)

    assert caught.value.code == "SIZING_VALUE_INVALID"


def test_unexpected_failure_rolls_back_and_is_not_recorded(session_factory):
    def executor(session, command):
        session.query(Account).filter(Account.id == 1).one().current_cash = 1
        raise RuntimeError("database password=secret")

    with pytest.raises(TradeGatewayError) as caught:
        _gateway(session_factory, executor).execute(_command())

    assert caught.value.code == "TRADE_GATEWAY_INFRASTRUCTURE_ERROR"
    assert "secret" not in str(caught.value)
    with session_factory() as session:
        assert float(session.query(Account).filter(Account.id == 1).one().current_cash) == 10000
        assert session.query(TradeCommandReceipt).count() == 0


def test_business_reject_is_durable_and_not_reexecuted(session_factory):
    calls = []

    def executor(session, command):
        calls.append(command)
        session.query(Account).filter(Account.id == 1).one().current_cash = 1
        return {"executed": False, "error": "US market is closed for AAPL"}

    command = _command(market=Market.US, symbol="AAPL")
    first = _gateway(session_factory, executor).execute(command)
    second = _gateway(session_factory, executor).execute(command)

    assert first.accepted is False
    assert first.reject_code == "MARKET_CLOSED"
    assert second == first
    assert len(calls) == 1
    with session_factory() as session:
        assert float(session.query(Account).filter(Account.id == 1).one().current_cash) == 10000
        assert session.query(TradeCommandReceipt).count() == 1


def test_reusing_key_for_different_command_fails_explicitly(session_factory):
    gateway = _gateway(
        session_factory,
        lambda session, command: {"executed": True, "order_id": 1},
    )
    gateway.execute(_command())

    with pytest.raises(TradeGatewayError) as caught:
        gateway.execute(_command(symbol="ETH"))
    assert caught.value.code == "TRADE_IDEMPOTENCY_KEY_REUSED"


def test_concurrent_gateway_instances_execute_only_once(session_factory):
    entered = Event()
    release = Event()
    calls = 0
    lock = Lock()

    def executor(session, command):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return {"executed": True, "order_id": 9}

    first_gateway = _gateway(session_factory, executor)
    second_gateway = _gateway(session_factory, executor)
    command = _command(key="concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first_gateway.execute, command)
        assert entered.wait(timeout=5)
        second_future = pool.submit(second_gateway.execute, command)
        release.set()
        first = first_future.result(timeout=10)
        second = second_future.result(timeout=10)

    assert first == second
    assert calls == 1


def test_legacy_execution_failure_rolls_back_every_financial_write(
    session_factory,
    monkeypatch,
):
    from services.agent import trade_execution_tool
    from services import order_executor_leverage

    monkeypatch.setattr(trade_execution_tool, "get_last_price", lambda *args: 100.0)
    monkeypatch.setattr(trade_execution_tool, "calc_positions_value", lambda *args: 0.0)
    monkeypatch.setattr(order_executor_leverage, "get_last_price", lambda *args: 100.0)
    monkeypatch.setattr(
        trade_execution_tool,
        "_save_trade_log",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit insert failed")),
    )

    with pytest.raises(TradeGatewayError):
        _gateway(session_factory).execute(_command(key="rollback"))

    with session_factory() as session:
        account = session.query(Account).filter(Account.id == 1).one()
        assert float(account.current_cash) == 10000
        assert session.query(Order).count() == 0
        assert session.query(Trade).count() == 0
        assert session.query(Position).count() == 0
        assert session.query(TradeCommandReceipt).count() == 0


def test_legacy_execution_and_receipt_commit_atomically(session_factory, monkeypatch):
    from services.agent import trade_execution_tool
    from services import order_executor_leverage

    monkeypatch.setattr(trade_execution_tool, "get_last_price", lambda *args: 100.0)
    monkeypatch.setattr(trade_execution_tool, "calc_positions_value", lambda *args: 0.0)
    monkeypatch.setattr(order_executor_leverage, "get_last_price", lambda *args: 100.0)

    gateway = _gateway(session_factory)
    first = gateway.execute(_command(key="actual"))
    second = _gateway(session_factory).execute(_command(key="actual"))

    assert first == second
    with session_factory() as session:
        assert session.query(Order).count() == 1
        assert session.query(Trade).count() == 1
        assert session.query(Position).count() == 1
        assert session.query(AIDecisionLog).count() == 1
        assert session.query(TradeCommandReceipt).count() == 1


def test_internal_create_cancel_and_pending_commands_share_uow(
    session_factory,
    monkeypatch,
):
    from services import order_matching

    monkeypatch.setattr(order_matching, "get_last_price", lambda *args: 100.0)
    gateway = _gateway(session_factory)
    created = gateway.create_order(
        CreateOrderCommand(
            account_id=1,
            symbol="BTC",
            market=Market.CRYPTO,
            side="BUY",
            order_type="LIMIT",
            quantity=Decimal("1"),
            price=Decimal("101"),
        )
    )
    assert created.accepted is True

    processed = gateway.process_pending(ProcessPendingOrders(account_id=1))
    assert processed.processed == 1
    assert processed.executed == 1

    second = gateway.create_order(
        CreateOrderCommand(
            account_id=1,
            symbol="BTC",
            market=Market.CRYPTO,
            side="BUY",
            order_type="LIMIT",
            quantity=Decimal("1"),
            price=Decimal("90"),
        )
    )
    with session_factory() as session:
        order_no = session.query(Order).filter(Order.id == second.order_id).one().order_no
    cancelled = gateway.cancel_order(CancelOrderCommand(1, order_no, "test"))
    assert cancelled.accepted is True
    with session_factory() as session:
        assert session.query(Order).filter(Order.id == second.order_id).one().status == "CANCELLED"


def test_cancel_and_pending_use_account_first_lock_order(session_factory, monkeypatch):
    from benchmark.persistence.sqlalchemy_repositories import (
        SqlAlchemyAccountRepository,
        SqlAlchemyOrderRepository,
    )
    from services import order_matching

    monkeypatch.setattr(order_matching, "get_last_price", lambda *args: 100.0)
    gateway = _gateway(session_factory)
    created = gateway.create_order(
        CreateOrderCommand(
            account_id=1,
            symbol="BTC",
            market=Market.CRYPTO,
            side="BUY",
            order_type="LIMIT",
            quantity=Decimal("1"),
            price=Decimal("90"),
        )
    )
    with session_factory() as session:
        order_no = session.query(Order).filter(Order.id == created.order_id).one().order_no

    events = []
    original_account_lock = SqlAlchemyAccountRepository.get_for_update
    original_order_lock = SqlAlchemyOrderRepository.get_by_no_for_update
    original_pending_lock = SqlAlchemyOrderRepository.list_pending_for_update

    def account_lock(self, account_id):
        events.append("account")
        return original_account_lock(self, account_id)

    def order_lock(self, value):
        events.append("order")
        return original_order_lock(self, value)

    def pending_lock(self, account_id=None):
        events.append("orders")
        return original_pending_lock(self, account_id)

    monkeypatch.setattr(SqlAlchemyAccountRepository, "get_for_update", account_lock)
    monkeypatch.setattr(SqlAlchemyOrderRepository, "get_by_no_for_update", order_lock)
    monkeypatch.setattr(SqlAlchemyOrderRepository, "list_pending_for_update", pending_lock)

    gateway.cancel_order(CancelOrderCommand(1, order_no, "test"))
    assert events[:2] == ["account", "order"]

    second = gateway.create_order(
        CreateOrderCommand(
            account_id=1,
            symbol="BTC",
            market=Market.CRYPTO,
            side="BUY",
            order_type="LIMIT",
            quantity=Decimal("1"),
            price=Decimal("90"),
        )
    )
    assert second.accepted is True
    events.clear()
    gateway.process_pending(ProcessPendingOrders(account_id=1))
    assert events[:2] == ["account", "orders"]


def test_order_provider_failure_is_rejected_without_leaking_details(
    session_factory,
    monkeypatch,
):
    from services import order_matching

    monkeypatch.setattr(
        order_matching,
        "get_last_price",
        lambda *args: (_ for _ in ()).throw(RuntimeError("api_key=secret")),
    )
    result = _gateway(session_factory).create_order(
        CreateOrderCommand(
            account_id=1,
            symbol="BTC",
            market=Market.CRYPTO,
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("1"),
        )
    )
    assert result.accepted is False
    assert result.reject_code == "PRICE_UNAVAILABLE"
    assert "secret" not in result.reject_message


def test_corrupt_pending_order_fails_explicitly_and_rolls_back(session_factory):
    with session_factory() as session:
        order = Order(
            version="v1",
            account_id=1,
            order_no="CORRUPT-1",
            symbol="BTC",
            name="BTC",
            market="CRYPTO",
            side="BUY",
            order_type="MARKET",
            price=None,
            quantity=1,
            leverage=1,
            filled_quantity=0,
            status="PENDING",
        )
        session.add(order)
        session.commit()

    with pytest.raises(TradeGatewayError) as caught:
        _gateway(session_factory).cancel_order(
            CancelOrderCommand(1, "CORRUPT-1", "test")
        )
    assert caught.value.code == "TRADE_GATEWAY_INFRASTRUCTURE_ERROR"
    with session_factory() as session:
        assert session.query(Order).filter(Order.order_no == "CORRUPT-1").one().status == "PENDING"


def test_committed_trade_is_not_reported_as_rejected_when_session_refresh_fails():
    from services.agent.trade_execution_tool import execute_trade_tool

    class Gateway:
        def execute(self, command):
            return TradeCommandResult(True, True, None, None, 1, 1, command)

    class BrokenCallerSession:
        def expire_all(self):
            raise ValueError("driver detail")

    with pytest.raises(TradeGatewayError) as caught:
        execute_trade_tool(
            db=BrokenCallerSession(),
            account_id=1,
            operation="hold",
            idempotency_key="round:call",
            gateway=Gateway(),
        )
    assert caught.value.code == "TRADE_CALLER_SESSION_REFRESH_FAILED"
    assert "driver detail" not in str(caught.value)
