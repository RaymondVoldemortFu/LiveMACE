"""Select active Agent accounts while keeping baselines on their own schedule."""

from services.baselines import is_baseline_trading_account
from repositories.account_repo import list_active_ai_accounts


def select_agent_accounts(db, requested_account_ids=None):
    wanted = None if requested_account_ids is None else set(requested_account_ids)
    return [
        account
        for account in list_active_ai_accounts(db)
        if (wanted is None or account.id in wanted)
        and not is_baseline_trading_account(account)
    ]


def select_manual_account_ids(requested):
    """Explicit operator selection, without enabling recurring AI scheduling."""
    from database.connection import SessionLocal
    from database.models import Account

    with SessionLocal() as db:
        accounts = (
            db.query(Account)
            .filter(Account.id.in_(requested), Account.is_active == "true")
            .all()
        )
        ids = [a.id for a in accounts if not is_baseline_trading_account(a)]
    if set(ids) != set(requested):
        raise ValueError("Select active non-baseline accounts")
    return ids
