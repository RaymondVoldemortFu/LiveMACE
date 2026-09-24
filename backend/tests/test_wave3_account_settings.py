"""Account edits preserve credentials and cannot erase an active AI model setup."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import account_routes
from config.api_feature_config import ApiFeatureConfig
from database.connection import get_db
from database.models import Account
from trading import test_gateway_reliability as reliability

session_factory = reliability.session_factory


@pytest.fixture
def client(session_factory, monkeypatch):
    from services import scheduler

    monkeypatch.setattr(ApiFeatureConfig, "ENABLE_ACCOUNT_UPDATE_API", True)
    monkeypatch.setattr(scheduler, "reset_auto_trading_job", lambda: None)
    with session_factory() as db:
        account = db.get(Account, 1)
        account.model = "deepseek-flash"
        account.base_url = "https://provider.test/v1"
        account.api_key = "enc$v1$stored-test-key"
        account.agent_type = "react"
        db.commit()

    def db_session():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(account_routes.router)
    app.dependency_overrides[get_db] = db_session
    with TestClient(app) as http:
        yield http


@pytest.mark.parametrize("field", ["model", "base_url"])
@pytest.mark.parametrize("value", [None, "", "   ", 123])
def test_active_ai_rejects_empty_model_fields_atomically(client, session_factory, field, value):
    result = client.put("/api/account/1", json={"name": "changed", field: value})
    assert result.status_code == 400
    assert "non-empty model and base_url" in result.json()["detail"]
    with session_factory() as db:
        account = db.get(Account, 1)
        assert account.name == "gateway-account"
        assert account.model == "deepseek-flash"
        assert account.base_url == "https://provider.test/v1"
        assert account.api_key == "enc$v1$stored-test-key"


@pytest.mark.parametrize("changes", [
    {"name": "Renamed account"},
    {"model": "updated-model", "base_url": "https://updated-provider.test/v1"},
])
def test_valid_ai_edit_with_omitted_api_key_preserves_stored_ciphertext(client, session_factory, changes):
    result = client.put("/api/account/1", json=changes)
    assert result.status_code == 200
    assert result.json()["api_key"] == "ENC(****)"
    with session_factory() as db:
        account = db.get(Account, 1)
        assert account.api_key == "enc$v1$stored-test-key"
        for field, value in changes.items():
            assert getattr(account, field) == value


@pytest.mark.parametrize("account_type,agent_type,name", [
    ("MANUAL", "react", "Manual account"),
    ("AI", "buy_hold", "Buy and hold baseline"),
    ("AI", "grid", "Grid baseline"),
    ("AI", "react", "buy_hold"),
])
def test_manual_and_baseline_accounts_can_keep_empty_model_setup(
    client, session_factory, account_type, agent_type, name,
):
    with session_factory() as db:
        db.get(Account, 1).account_type = account_type
        db.commit()
    result = client.put("/api/account/1", json={
        "name": name, "agent_type": agent_type, "model": "", "base_url": "",
    })
    assert result.status_code == 200
    with session_factory() as db:
        account = db.get(Account, 1)
        assert account.model is None
        assert account.base_url == ""
        assert account.api_key == "enc$v1$stored-test-key"


def test_baseline_switch_to_ai_requires_effective_model_fields(client, session_factory):
    with session_factory() as db:
        account = db.get(Account, 1)
        account.agent_type = "grid"
        account.model = None
        account.base_url = None
        db.commit()
    result = client.put("/api/account/1", json={"agent_type": "react"})
    assert result.status_code == 400
    with session_factory() as db:
        assert db.get(Account, 1).agent_type == "grid"


def test_unrelated_partial_edit_preserves_legacy_unconfigured_account(client, session_factory):
    with session_factory() as db:
        account = db.get(Account, 1)
        account.model = None
        account.base_url = None
        db.commit()
    result = client.put("/api/account/1", json={"name": "Legacy renamed"})
    assert result.status_code == 200
    with session_factory() as db:
        account = db.get(Account, 1)
        assert account.name == "Legacy renamed"
        assert account.model is account.base_url is None
        assert account.api_key == "enc$v1$stored-test-key"
