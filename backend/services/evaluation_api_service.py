from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.orm import Session

from benchmark.persistence.sqlalchemy_repositories import (
    SqlAlchemyAccountRepository,
    SqlAlchemyEvaluationRepository,
)
from database.models import Account


class AccountNotFoundError(LookupError):
    pass


class InvalidLeaderboardOrderError(ValueError):
    pass


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _to_naive_utc(value: datetime) -> datetime:
    return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


class EvaluationApiService:
    def __init__(self, db: Session):
        def provider() -> Session:
            return db

        self._accounts = SqlAlchemyAccountRepository(provider)
        self._evaluations = SqlAlchemyEvaluationRepository(provider)

    def list_account_checkpoints(self, account_id: int, interval_seconds: int, limit: int):
        account = self._accounts.get(account_id)
        if not account:
            raise AccountNotFoundError("Account not found")
        rows = self._evaluations.list_recent(account_id, interval_seconds, limit)
        return {
            "account_id": account_id,
            "account_name": account.name,
            "interval_seconds": interval_seconds,
            "items": [
                {
                    "period_start": _to_iso_utc(row.period_start),
                    "period_end": _to_iso_utc(row.period_end),
                    "equity_start": _to_float(row.equity_start),
                    "equity_end": _to_float(row.equity_end),
                    "pnl": _to_float(row.pnl),
                    "return_rate": row.return_rate,
                    "volatility": getattr(row, "volatility", None),
                    "created_at": _to_iso_utc(row.created_at),
                }
                for row in rows
            ],
        }

    def leaderboard(self, interval_seconds: int, period_end: Optional[datetime], limit: int, order_by: str):
        order_by = (order_by or "return").strip().lower()
        if order_by not in {"return", "pnl", "volatility"}:
            raise InvalidLeaderboardOrderError("order_by must be 'return', 'pnl', or 'volatility'")
        if period_end is None:
            period_end = self._evaluations.latest_period_end(interval_seconds)
            if period_end is None:
                return {"interval_seconds": interval_seconds, "period_end": None, "items": []}
        else:
            period_end = _to_naive_utc(period_end)
        rows = self._evaluations.list_period(interval_seconds, period_end, order_by, limit)
        accounts = self._account_map([row.account_id for row in rows])
        return {
            "interval_seconds": interval_seconds,
            "period_end": _to_iso_utc(period_end),
            "order_by": order_by,
            "items": [
                {
                    "account_id": row.account_id,
                    "agent_name": accounts[row.account_id].name if row.account_id in accounts else None,
                    "agent_type": getattr(accounts.get(row.account_id), "agent_type", None),
                    "equity_start": _to_float(row.equity_start),
                    "equity_end": _to_float(row.equity_end),
                    "pnl": _to_float(row.pnl),
                    "return_rate": row.return_rate,
                    "volatility": getattr(row, "volatility", None),
                }
                for row in rows
            ],
        }

    def compare_agents(
        self,
        interval_seconds: int,
        start: Optional[datetime],
        end: Optional[datetime],
        limit: int,
    ):
        start_naive = _to_naive_utc(start) if start is not None else None
        end_naive = _to_naive_utc(end) if end is not None else None
        rows = self._evaluations.list_between(interval_seconds, start_naive, end_naive, limit)
        if not rows:
            return {"interval_seconds": interval_seconds, "items": []}
        accounts = self._account_map(sorted({row.account_id for row in rows}))
        return {
            "interval_seconds": interval_seconds,
            "items": [
                {
                    "account_id": row.account_id,
                    "agent_name": accounts[row.account_id].name if row.account_id in accounts else None,
                    "period_start": _to_iso_utc(row.period_start),
                    "period_end": _to_iso_utc(row.period_end),
                    "pnl": _to_float(row.pnl),
                    "return_rate": row.return_rate,
                    "volatility": getattr(row, "volatility", None),
                }
                for row in rows
            ],
        }

    def _account_map(self, account_ids) -> Dict[int, Account]:
        accounts = self._accounts.get_many(list(account_ids))
        return {account.id: account for account in accounts}
