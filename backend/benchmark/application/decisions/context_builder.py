"""Decision context helpers kept small during M10 migration."""

from __future__ import annotations

from database.models import Account


def build_decision_trace_id(account: Account, decision_round_id: str) -> str:
    return f"{decision_round_id}:account:{account.id}"


__all__ = ["build_decision_trace_id"]
