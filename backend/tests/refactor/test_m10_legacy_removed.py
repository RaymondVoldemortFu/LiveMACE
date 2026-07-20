from __future__ import annotations

from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (BACKEND_ROOT / relative).read_text(encoding="utf-8")


def test_m10_legacy_json_symbols_are_removed_from_production_sources():
    production_sources = [
        "services/ai_decision_service.py",
        "services/trading_commands.py",
        "services/auto_trader.py",
        "config/agent_config.py",
    ]
    forbidden = [
        "def call_ai_for_decision",
        "call_ai_for_decision,",
        "AgentConfig.USE_AGENT",
        "USE_AGENT =",
        'protocol == "tool"',
        '"executed_trades" in decision',
    ]

    violations = []
    for relative in production_sources:
        text = _read(relative)
        for pattern in forbidden:
            if pattern in text:
                violations.append(f"{relative}: {pattern}")

    assert violations == []


def test_m10_decision_payload_processor_no_longer_contains_order_execution_path():
    text = _read("services/trading_commands.py")
    start = text.index("def _process_account_decision_payload")
    end = text.index("def place_ai_driven_crypto_order")
    function_body = text[start:end]

    assert "save_ai_decision" in function_body
    assert "create_order" not in function_body
    assert "place_and_execute_crypto" not in function_body
    assert "check_and_execute_order" not in function_body


def test_m10_decision_payload_persists_zero_execution_values_and_positive_order_id(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("ALPACA_KEY", "dummy")
    monkeypatch.setenv("ALPACA_SECRET", "dummy")

    from services import trading_commands

    saved = {}
    account = SimpleNamespace(id=1, name="agent", agent_type="react")

    monkeypatch.setattr(trading_commands, "get_account", lambda db, account_id: account)
    monkeypatch.setattr(trading_commands, "is_baseline_trading_account", lambda account: False)

    def fake_save_ai_decision(db, account_id, decision, portfolio, **kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(trading_commands, "save_ai_decision", fake_save_ai_decision)

    trading_commands._process_account_decision_payload(
        db=object(),
        payload={
            "account_id": 1,
            "portfolio": {},
            "decision": {
                "executed": True,
                "order_id": "0",
                "execution_price": 0,
                "execution_quantity": 0.0,
            },
        },
        prices={},
    )

    assert saved["order_id"] is None
    assert saved["execution_price"] == 0.0
    assert saved["execution_quantity"] == 0.0

    saved.clear()
    trading_commands._process_account_decision_payload(
        db=object(),
        payload={
            "account_id": 1,
            "portfolio": {},
            "decision": {
                "executed": True,
                "order_id": "42",
                "execution_price": "0",
                "execution_quantity": "0",
            },
        },
        prices={},
    )

    assert saved["order_id"] == 42
    assert saved["execution_price"] == 0.0
    assert saved["execution_quantity"] == 0.0

