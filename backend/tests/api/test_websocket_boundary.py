from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.ws import ConnectionManager
from schemas.websocket import parse_websocket_message


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "bootstrap"},
        {"type": "subscribe", "user_id": 1},
        {"type": "switch_user", "username": "alice"},
        {"type": "switch_account", "account_id": 2},
        {"type": "get_snapshot"},
        {"type": "get_asset_curve", "timeframe": "1h"},
        {
            "type": "place_order",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 0.1,
        },
        {"type": "ping"},
    ],
)
def test_existing_client_message_types_are_accepted(payload):
    assert parse_websocket_message(payload).type == payload["type"]


def test_invalid_order_is_rejected_before_dispatch():
    with pytest.raises(ValidationError):
        parse_websocket_message(
            {
                "type": "place_order",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": -1,
            }
        )


def test_order_leverage_preserves_the_existing_one_to_fifty_contract():
    accepted = parse_websocket_message(
        {
            "type": "place_order",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 0.1,
            "leverage": 50,
        }
    )
    assert accepted.leverage == 50

    with pytest.raises(ValidationError):
        parse_websocket_message(
            {
                "type": "place_order",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": 0.1,
                "leverage": 51,
            }
        )


def test_connection_manager_keys_are_account_ids(monkeypatch):
    scheduled = []
    removed = []
    monkeypatch.setattr("api.ws.add_account_snapshot_job", lambda account_id, **_: scheduled.append(account_id))
    monkeypatch.setattr("api.ws.remove_account_snapshot_job", removed.append)
    manager = ConnectionManager()
    websocket = object()

    manager.register(42, websocket)
    manager.unregister(42, websocket)

    assert scheduled == [42]
    assert removed == [42]
    assert manager.active_connections == {}
