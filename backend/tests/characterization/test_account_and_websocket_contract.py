from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from api import account_routes
from api import ws
from services import order_matching


class _ListQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *args):
        return self

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


def test_account_list_flag_serialization_is_characterized():
    account = SimpleNamespace(
        id=1,
        user_id=7,
        name="agent",
        account_type="AI",
        agent_type="react",
        memory_enabled="true",
        tool_routing_enabled="false",
        enable_rule_aware="true",
        initial_capital=10000,
        current_cash=9000,
        frozen_cash=0,
        model="fake",
        base_url=None,
        api_key=None,
        is_active="true",
    )
    user = SimpleNamespace(id=7, username="default")

    class DB:
        def query(self, model):
            return _ListQuery([user] if model.__name__ == "User" else [account])

    result = asyncio.run(account_routes.list_all_accounts(DB()))
    assert result[0]["memory_enabled"] == "true"
    assert result[0]["tool_routing_enabled"] == "false"
    assert result[0]["enable_rule_aware"] is True
    assert result[0]["is_active"] is True


def test_websocket_bootstrap_switch_snapshot_curve_and_order_message_contract(monkeypatch):
    sent = []
    sessions = []
    snapshots = []
    account = SimpleNamespace(id=11, user_id=7, name="default AI Trader")
    target_account = SimpleNamespace(id=12, user_id=7, name="second AI Trader")
    user = SimpleNamespace(id=7, username="default")

    class FakeDB:
        closed = False

        def commit(self):
            return None

        def close(self):
            self.closed = True

    class FakeWebSocket:
        client_state = SimpleNamespace(name="CONNECTED")

        def __init__(self):
            self.messages = iter(
                [
                    {"type": "bootstrap", "username": "default", "initial_capital": 10000},
                    {"type": "switch_account", "account_id": 12},
                    {"type": "get_snapshot"},
                    {"type": "get_asset_curve", "timeframe": "1h"},
                    {
                        "type": "place_order",
                        "symbol": "BTC",
                        "market": "CRYPTO",
                        "side": "BUY",
                        "order_type": "LIMIT",
                        "price": 90,
                        "quantity": 0.1,
                        "leverage": 1,
                    },
                ]
            )

        async def accept(self):
            return None

        async def receive_text(self):
            try:
                return json.dumps(next(self.messages))
            except StopIteration:
                raise WebSocketDisconnect()

        async def send_text(self, payload):
            sent.append(json.loads(payload))

    def session_factory():
        session = FakeDB()
        sessions.append(session)
        return session

    async def record_snapshot(db, account_id):
        snapshots.append(account_id)

    monkeypatch.setattr(ws, "SessionLocal", session_factory)
    monkeypatch.setattr(ws, "get_or_create_user", lambda db, username: user)
    monkeypatch.setattr(ws, "get_or_create_default_account", lambda *args, **kwargs: account)
    monkeypatch.setattr(
        ws,
        "get_account",
        lambda db, account_id: target_account if int(account_id) == 12 else account,
    )
    monkeypatch.setattr(ws, "get_user", lambda db, user_id: user)
    monkeypatch.setattr(ws, "get_all_asset_curves_data", lambda db, timeframe: [{"timestamp": 1}])
    monkeypatch.setattr(ws, "_send_snapshot", record_snapshot)
    monkeypatch.setattr(ws, "add_account_snapshot_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(ws, "remove_account_snapshot_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(ws, "manager", ws.ConnectionManager())
    monkeypatch.setattr(
        order_matching,
        "create_order",
        lambda **kwargs: SimpleNamespace(id=99),
    )

    asyncio.run(ws.websocket_endpoint(FakeWebSocket()))

    assert [item["type"] for item in sent] == [
        "bootstrap_ok",
        "account_switched",
        "asset_curve_data",
        "order_pending",
    ]
    assert sent[0]["account"] == {"id": 11, "name": "default AI Trader", "user_id": 7}
    assert sent[1]["account"] == {"id": 12, "name": "second AI Trader", "user_id": 7}
    assert sent[2] == {"type": "asset_curve_data", "timeframe": "1h", "data": [{"timestamp": 1}]}
    assert sent[3] == {"type": "order_pending", "order_id": 99}
    assert snapshots == [11, 12, 12, 12]
    assert sessions and all(session.closed for session in sessions)
