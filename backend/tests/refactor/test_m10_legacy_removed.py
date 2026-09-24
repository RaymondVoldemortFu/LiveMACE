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
        "def call_agent_for_decision",
        "def _collect_account_decision",
        "def _process_account_decision_payload",
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


def test_m10_worker_summary_has_no_order_execution_path():
    text = _read("benchmark/persistence/decision_summary.py")
    summary = text[text.index("def save_run_summary("):]
    assert "save_ai_decision" in summary
    assert "create_order" not in summary
    assert "place_and_execute_crypto" not in summary
    assert "check_and_execute_order" not in summary
