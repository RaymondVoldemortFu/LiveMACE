"""Account selection helpers for decision rounds."""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import Account
from services.baselines import is_baseline_trading_account


AGENT_DECISION_TYPES = {"react", "multi_agent", "advanced_multi_agent", "rule_aware"}


def select_agent_accounts(db: Session, requested_account_ids: tuple[int, ...] | None = None) -> list[Account]:
    from services.trading_commands import _load_trading_accounts

    accounts = _load_trading_accounts(db)
    if requested_account_ids is not None:
        wanted = set(requested_account_ids)
        accounts = [account for account in accounts if account.id in wanted]
    return [
        account
        for account in accounts
        if str(getattr(account, "agent_type", "react") or "react").strip().lower() in AGENT_DECISION_TYPES
        and not is_baseline_trading_account(account)
    ]


__all__ = ["AGENT_DECISION_TYPES", "select_agent_accounts"]