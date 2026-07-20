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
