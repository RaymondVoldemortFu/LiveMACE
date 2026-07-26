"""Account selection helpers for decision rounds."""

from __future__ import annotations

from sqlalchemy.orm import Session

from database.models import Account
from services.baselines import is_baseline_trading_account

def select_agent_accounts(db: Session, requested_account_ids: tuple[int, ...] | None = None) -> list[Account]:
    from services.trading_commands import _load_trading_accounts

    accounts = _load_trading_accounts(db)
    if requested_account_ids is not None:
        wanted = set(requested_account_ids)
        accounts = [account for account in accounts if account.id in wanted]
    # TODO: Replace the baseline exclusion fallback with an explicit agent capability registry
    # once agent metadata/discovery interfaces are available.
    return [account for account in accounts if not is_baseline_trading_account(account)]


__all__ = ["select_agent_accounts"]
