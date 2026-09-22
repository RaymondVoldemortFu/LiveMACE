"""Consecutive complete-cycle rules for the Wave3 scheduler watcher."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from wave3_watch_cycles import is_complete_cycle, update_consecutive_cycles


def _account(aid: int, *, event="run.result", reason="trade_done") -> dict:
    return {
        "account_id": aid,
        "event_type": event,
        "termination_reason": reason,
    }


def _round(reasons: dict[int, str]) -> dict:
    return {aid: _account(aid, reason=reason) for aid, reason in reasons.items()}


def test_complete_cycle_requires_hold_or_trade_done_for_all_accounts():
    assert is_complete_cycle(
        _round({1: "trade_done", 2: "hold", 3: "trade_done", 4: "hold"})
    )
    assert not is_complete_cycle(
        _round({1: "trade_done", 2: "hold", 3: "trade_done", 4: "max_steps"})
    )
    assert not is_complete_cycle(
        _round({1: "trade_done", 2: "hold", 3: "trade_done", 4: "tool_error"})
    )
    assert not is_complete_cycle(
        {
            1: _account(1, event="run.failed", reason=None),
            2: _account(2, reason="hold"),
            3: _account(3, reason="trade_done"),
            4: _account(4, reason="trade_done"),
        }
    )


def test_incomplete_cycle_resets_consecutive_count():
    first = _round({1: "trade_done", 2: "hold", 3: "trade_done", 4: "hold"})
    second = _round({1: "trade_done", 2: "hold", 3: "trade_done", 4: "max_steps"})
    third = _round({1: "hold", 2: "hold", 3: "trade_done", 4: "trade_done"})
    cycles = update_consecutive_cycles([], first)
    assert len(cycles) == 1
    cycles = update_consecutive_cycles(cycles, second)
    assert cycles == []
    cycles = update_consecutive_cycles(cycles, third)
    assert len(cycles) == 1
