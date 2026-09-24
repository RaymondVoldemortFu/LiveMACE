from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.extension_routes import router as extension_router
from api.order_routes import router as order_router
from benchmark.application.trading import CreateOrderCommand, SynchronousTradeCommandGateway
from benchmark.application.trading.policy import normalize_trade_command
from benchmark.contracts import Market, TradeCommand
from benchmark.contracts.errors import TradeGatewayError
from benchmark.extensions import ExtensionSettings, build_extension_runtime
from benchmark.persistence import SqlAlchemyUnitOfWork
from config.api_feature_config import ApiFeatureConfig
from database.connection import Base
from database.models import Account, AccountRuntimeConfig, Order, User, UserAuthSession
from schemas.websocket import parse_websocket_message
from services import websocket_session_service
from services.extension_config_service import ExtensionConfigService, get_extension_config_service
from services.http_order_service import HttpOrderService, get_http_order_service


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, username="trader"))
        db.flush()
        db.add(Account(
            id=1, user_id=1, name="trader", initial_capital=10000,
            current_cash=10000, frozen_cash=0,
        ))
        db.add(UserAuthSession(
            user_id=1, session_token="test-session",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
        ))
        db.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def gateway(session_factory):
    return SynchronousTradeCommandGateway(lambda: SqlAlchemyUnitOfWork(session_factory))


@pytest.fixture
def client(session_factory, gateway, monkeypatch):
    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_ACCOUNT_UPDATE_API", True)
    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_MANUAL_ORDER_API", True)
    extension_service = ExtensionConfigService(
        build_extension_runtime(ExtensionSettings()),
        lambda: SqlAlchemyUnitOfWork(session_factory),
    )
    order_service = HttpOrderService(gateway, session_factory)
    app = FastAPI()
    app.include_router(extension_router)
    app.include_router(order_router)
    app.dependency_overrides[get_extension_config_service] = lambda: extension_service
    app.dependency_overrides[get_http_order_service] = lambda: order_service
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


CONFIG_URL = "/api/account/1/runtime-config"
CONFIG = {"agent_id": "core.react", "agent_config": {}}


def test_runtime_config_token_round_trip_and_stale_update(client):
    first = client.put(CONFIG_URL, json={"config": CONFIG})
    assert first.status_code == 200
    first_token = first.json()["updated_at"]
    assert datetime.fromisoformat(first_token).utcoffset() == timedelta(0)
    assert client.get(CONFIG_URL).json()["updated_at"] == first_token

    updated_config = {**CONFIG, "prompt_profile_id": "core.react.default"}
    second = client.put(CONFIG_URL, json={
        "config": updated_config, "expected_updated_at": first_token,
    })
    assert second.status_code == 200
    second_token = second.json()["updated_at"]
    assert datetime.fromisoformat(second_token) > datetime.fromisoformat(first_token)

    stale = client.put(CONFIG_URL, json={"config": CONFIG, "expected_updated_at": first_token})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "RUNTIME_CONFIG_CONFLICT"
    saved = client.get(CONFIG_URL).json()
    assert saved["updated_at"] == second_token
    assert saved["config"]["prompt_profile_id"] == "core.react.default"


@pytest.mark.parametrize("token_format", ["naive", "offset"])
def test_runtime_config_accepts_legacy_and_equivalent_utc_tokens(client, token_format):
    first = client.put(CONFIG_URL, json={"config": CONFIG})
    timestamp = datetime.fromisoformat(first.json()["updated_at"])
    timestamp = (
        timestamp.replace(tzinfo=None) if token_format == "naive"
        else timestamp.astimezone(timezone(timedelta(hours=8)))
    )
    response = client.put(CONFIG_URL, json={
        "config": CONFIG, "expected_updated_at": timestamp.isoformat(),
    })
    assert response.status_code == 200


def test_runtime_config_write_switch_preserves_reads_and_validation(client, session_factory, monkeypatch):
    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_ACCOUNT_UPDATE_API", False)
    response = client.put(CONFIG_URL, json={"config": CONFIG})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_UPDATE_DISABLED"
    assert client.get(CONFIG_URL).status_code == 200
    assert client.post(f"{CONFIG_URL}/validate", json={"config": CONFIG}).json()["valid"] is True
    with session_factory() as db:
        assert db.query(AccountRuntimeConfig).count() == 0


def test_runtime_config_disabled_update_preserves_existing_row(client, monkeypatch):
    saved = client.put(CONFIG_URL, json={"config": CONFIG}).json()
    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_ACCOUNT_UPDATE_API", False)
    response = client.put(CONFIG_URL, json={
        "config": {**CONFIG, "prompt_profile_id": "core.react.default"},
        "expected_updated_at": saved["updated_at"],
    })
    assert response.status_code == 403
    assert client.get(CONFIG_URL).json() == saved


@pytest.mark.parametrize("leverage", [1, 10, 11, 50])
def test_websocket_order_dispatch_persists_full_leverage_range(session_factory, gateway, monkeypatch, leverage):
    monkeypatch.setattr(websocket_session_service, "_gateway", lambda: gateway)
    message = parse_websocket_message({
        "type": "place_order", "symbol": "BTC", "side": "BUY",
        "order_type": "LIMIT", "price": 100, "quantity": 1, "leverage": leverage,
    })
    result = websocket_session_service.place_order(1, message.model_dump())
    assert result.accepted is True
    with session_factory() as db:
        order = db.get(Order, result.order_id)
        assert order.leverage == leverage
        assert order.status == "PENDING"


@pytest.mark.parametrize("market,leverage", [(Market.CRYPTO, 0), (Market.CRYPTO, 51), (Market.US, 2)])
def test_manual_order_leverage_limits(market, leverage):
    with pytest.raises(ValueError):
        CreateOrderCommand(
            account_id=1, symbol="AAPL" if market is Market.US else "BTC", market=market,
            side="BUY", order_type="LIMIT", quantity=Decimal("1"), price=Decimal("100"),
            leverage=leverage,
        )


def test_agent_trade_policy_keeps_ten_times_limit():
    command = TradeCommand(
        account_id=1, operation="open", symbol="BTC", market=Market.CRYPTO,
        direction="long", sizing_mode="usd", sizing_value=Decimal("100"),
        leverage=11, reason="test", idempotency_key="leverage-test",
    )
    with pytest.raises(TradeGatewayError) as caught:
        normalize_trade_command(command)
    assert caught.value.code == "LEVERAGE_INVALID"


def _order_payload(**changes):
    return {
        "user_id": 1, "session_token": "test-session", "symbol": "BTC", "name": "Bitcoin",
        "side": "BUY", "order_type": "LIMIT", "price": 100, "quantity": 1, **changes,
    }


@pytest.mark.parametrize("changes", [{"price": None}, {"price": 0}, {"quantity": 0}, {"quantity": -1}])
def test_invalid_order_parameters_return_400_without_writing(client, session_factory, changes):
    response = client.post("/api/orders/create", json=_order_payload(**changes))
    assert response.status_code == 400
    assert response.json()["detail"]
    with session_factory() as db:
        assert db.query(Order).count() == 0
        assert db.get(Account, 1).current_cash == Decimal("10000")


def test_order_infrastructure_failures_remain_server_errors(client, gateway, monkeypatch):
    def fail(command):
        raise TradeGatewayError("Trade gateway unavailable", code="TRADE_GATEWAY_INFRASTRUCTURE_ERROR")

    monkeypatch.setattr(gateway, "create_order", fail)
    response = client.post("/api/orders/create", json=_order_payload())
    assert response.status_code == 500


def test_settings_preserves_pinned_runtime_when_legacy_fields_are_unchanged(session_factory, monkeypatch):
    from api.account_routes import router as account_router
    from database.connection import get_db
    from services import scheduler
    from benchmark.accounts.service import _config_from_row

    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_ACCOUNT_UPDATE_API", True)
    monkeypatch.setattr(scheduler, "reset_auto_trading_job", lambda: None)
    extension_service = ExtensionConfigService(
        build_extension_runtime(ExtensionSettings()), lambda: SqlAlchemyUnitOfWork(session_factory))
    extension_service.save(1, {"agent_id": "core.react", "agent_config": {"max_steps": 6},
        "prompt_profile_id": "core.react.default", "disabled_tools": ["core.execute_trade"]}, None)
    app = FastAPI()
    app.include_router(account_router)

    def get_test_db():
        with session_factory() as db:
            yield db
    app.dependency_overrides[get_db] = get_test_db
    client = TestClient(app)
    with session_factory() as db:
        account = db.get(Account, 1)
        payload = {key: getattr(account, key) for key in ("agent_type", "memory_enabled", "tool_routing_enabled")}
        payload["enable_rule_aware"] = account.enable_rule_aware == "true"
        before = _config_from_row(db.query(AccountRuntimeConfig).one()).to_dict()
    response = client.put("/api/account/1", json={**payload, "name": "renamed"})
    assert response.status_code == 200, response.text
    with session_factory() as db:
        assert _config_from_row(db.query(AccountRuntimeConfig).one()).to_dict() == before
    response = client.put("/api/account/1", json={**payload, "tool_routing_enabled": "false"})
    assert response.status_code == 200, response.text
    with session_factory() as db:
        config = _config_from_row(db.query(AccountRuntimeConfig).one())
        assert config.agent_config["max_steps"] == 6
        assert config.disabled_tools == ("core.execute_trade",)
        assert config.agent_config.get("tool_routing_enabled", False) is False


def test_legacy_agent_field_change_keeps_same_effective_rule_aware_tuning(session_factory):
    from services.account_api_service import AccountApiService
    from benchmark.accounts.service import _config_from_row
    runtime = build_extension_runtime(ExtensionSettings())
    extension_service = ExtensionConfigService(runtime, lambda: SqlAlchemyUnitOfWork(session_factory))
    extension_service.save(1, {"agent_id": "core.rule-aware", "agent_config": {"max_steps": 6}}, None)
    with session_factory() as db:
        account = db.get(Account, 1)
        account.enable_rule_aware = "true"
        account.agent_type = "multi_agent"
        old = _config_from_row(db.query(AccountRuntimeConfig).one())
        AccountApiService(db).sync_runtime_switches(account, {"agent_type"})
        config = _config_from_row(db.query(AccountRuntimeConfig).one())
        assert config.agent_id == old.agent_id
        assert config.agent_config["max_steps"] == 6
        assert config.agent_version == old.agent_version


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_manual_crypto_orders_reject_existing_short_without_mutation(session_factory, gateway, side):
    from database.models import Position
    with session_factory() as db:
        db.add(Position(account_id=1, symbol="BTC", name="BTC", market="CRYPTO", side="SHORT",
            quantity=1, available_quantity=1, avg_cost=100, leverage=2))
        db.commit()
    result = gateway.create_order(CreateOrderCommand(account_id=1, symbol="BTC", market=Market.CRYPTO,
        side=side, order_type="LIMIT", quantity=Decimal("0.1"), price=Decimal("100")))
    assert result.accepted is False
    with session_factory() as db:
        position = db.query(Position).one()
        assert position.side == "SHORT" and position.quantity == 1
        assert db.query(Order).count() == 0
        assert db.get(Account, 1).current_cash == 10000
