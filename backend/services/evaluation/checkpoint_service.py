from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, List, Iterable
import logging
import math

from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import Account, AgentPeriodCheckpoint
from services.asset_calculator import calc_positions_market_value
from services.time_source import now_utc

logger = logging.getLogger(__name__)


VOLATILITY_LOOKBACK = 20


def _compute_return_rate_volatility(
    db: Session,
    account_id: int,
    interval_seconds: int,
    period_end: datetime,
    current_return_rate: float,
    lookback: int = VOLATILITY_LOOKBACK,
) -> float:
    """Compute rolling volatility as stddev of return_rate over last N checkpoints.

    Uses only checkpoints (as requested) and includes the current return_rate.
    If fewer than 2 samples, returns 0.0.
    """
    n = int(lookback)
    if n < 2:
        return 0.0

    prev_rows = (
        db.query(AgentPeriodCheckpoint.return_rate)
        .filter(
            AgentPeriodCheckpoint.account_id == account_id,
            AgentPeriodCheckpoint.interval_seconds == interval_seconds,
            AgentPeriodCheckpoint.period_end < period_end,
        )
        .order_by(AgentPeriodCheckpoint.period_end.desc())
        .limit(n - 1)
        .all()
    )

    rates: List[float] = [float(current_return_rate)]
    for (rr,) in prev_rows:
        if rr is None:
            continue
        rates.append(float(rr))

    if len(rates) < 2:
        return 0.0

    mean = sum(rates) / len(rates)
    var = sum((x - mean) ** 2 for x in rates) / len(rates)
    return math.sqrt(var)


def _list_active_ai_accounts(db: Session) -> List[Account]:
    # Account.is_active is stored as a string in this project; keep this tolerant
    # so we don't silently skip accounts due to casing/data drift.
    active_values = ["true", "True", "TRUE", "1", "yes", "YES", "y", "Y"]
    accounts = (
        db.query(Account)
        .filter(Account.account_type == "AI", Account.is_active.in_(active_values))
        .all()
    )
    if accounts:
        return accounts
    # Fallback: keep demo functional even if is_active values are inconsistent
    return db.query(Account).filter(Account.account_type == "AI").all()


@dataclass(frozen=True)
class PeriodBoundary:
    interval_seconds: int
    period_start: datetime
    period_end: datetime


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def align_to_interval_end(now: datetime, interval_seconds: int) -> PeriodBoundary:
    """Align current time to a fixed interval boundary (UTC) and return the current slice.

    Example: if interval=3600, then period_end is floored to the current hour.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")

    now_utc = _to_utc(now)
    ts = int(now_utc.timestamp())
    aligned_ts = ts - (ts % interval_seconds)
    period_end = datetime.fromtimestamp(aligned_ts, tz=timezone.utc)
    period_start = period_end - timedelta(seconds=interval_seconds)

    return PeriodBoundary(
        interval_seconds=interval_seconds,
        period_start=period_start,
        period_end=period_end,
    )


def compute_account_equity(db: Session, account: Account) -> Decimal:
    """Compute account equity = cash + position equity (includes unrealized PnL).

    Uses calc_positions_market_value() which handles leveraged positions correctly.
    """
    cash = Decimal(str(account.current_cash))
    positions_equity = Decimal(str(calc_positions_market_value(db, account.id)))
    return cash + positions_equity


def _get_latest_checkpoint(
    db: Session,
    account_id: int,
    interval_seconds: int,
) -> Optional[AgentPeriodCheckpoint]:
    return (
        db.query(AgentPeriodCheckpoint)
        .filter(
            AgentPeriodCheckpoint.account_id == account_id,
            AgentPeriodCheckpoint.interval_seconds == interval_seconds,
        )
        .order_by(AgentPeriodCheckpoint.period_end.desc())
        .first()
    )


def create_checkpoint_if_due(
    db: Session,
    account: Account,
    interval_seconds: int,
    now: Optional[datetime] = None,
) -> Optional[AgentPeriodCheckpoint]:
    """Create a new checkpoint for the current period_end if not already created.

    This is idempotent per (account_id, interval_seconds, period_end).
    equity_start is taken from the previous checkpoint's equity_end; if none exists,
    equity_start falls back to account.initial_capital.
    """
    # Use unified virtual time source across the system (supports DELTA_T_MINUTES simulation).
    now = now or now_utc()
    boundary = align_to_interval_end(now, interval_seconds)

    # Avoid writing the same period multiple times
    existing = (
        db.query(AgentPeriodCheckpoint)
        .filter(
            AgentPeriodCheckpoint.account_id == account.id,
            AgentPeriodCheckpoint.interval_seconds == interval_seconds,
            AgentPeriodCheckpoint.period_end == boundary.period_end.replace(tzinfo=None),
        )
        .first()
    )
    if existing:
        return None

    latest = _get_latest_checkpoint(db, account.id, interval_seconds)

    if latest:
        equity_start = Decimal(str(latest.equity_end))
    else:
        equity_start = Decimal(str(account.initial_capital))

    equity_end = compute_account_equity(db, account)
    pnl = equity_end - equity_start
    return_rate = float(pnl / equity_start) if equity_start > 0 else 0.0

    period_end_naive = boundary.period_end.replace(tzinfo=None)
    volatility = _compute_return_rate_volatility(
        db,
        account_id=account.id,
        interval_seconds=interval_seconds,
        period_end=period_end_naive,
        current_return_rate=return_rate,
    )

    checkpoint = AgentPeriodCheckpoint(
        account_id=account.id,
        interval_seconds=interval_seconds,
        period_start=boundary.period_start.replace(tzinfo=None),
        period_end=period_end_naive,
        equity_start=equity_start,
        equity_end=equity_end,
        pnl=pnl,
        return_rate=return_rate,
        volatility=volatility,
    )

    db.add(checkpoint)
    return checkpoint


def run_checkpoint_job(interval_seconds: int = 3600) -> int:
    """Scheduled job: create checkpoints for all active AI accounts for a single interval.

    Returns number of created checkpoints.
    """
    return run_checkpoint_jobs([interval_seconds])


def run_checkpoint_jobs(interval_seconds_list: Iterable[int]) -> int:
    """Create checkpoints for all active AI accounts for multiple intervals.

    This reuses one DB session and a single 'now' timestamp, keeping period alignment
    consistent within a poll.

    Returns number of created checkpoints in total.
    """
    intervals = [int(x) for x in interval_seconds_list if int(x) > 0]
    # De-duplicate while keeping deterministic order
    intervals = sorted(set(intervals))
    if not intervals:
        return 0

    db: Session = SessionLocal()
    created = 0
    # Use unified virtual time source across the system (supports DELTA_T_MINUTES simulation).
    now = now_utc()
    try:
        accounts: List[Account] = _list_active_ai_accounts(db)

        for interval_seconds in intervals:
            for account in accounts:
                try:
                    ckpt = create_checkpoint_if_due(
                        db,
                        account,
                        interval_seconds=interval_seconds,
                        now=now,
                    )
                    if ckpt is not None:
                        created += 1
                except Exception as e:
                    logger.error(
                        f"Checkpoint creation failed for account {account.id} ({account.name}) interval={interval_seconds}s: {e}",
                        exc_info=True,
                    )

        if created:
            db.commit()
        else:
            db.rollback()

        return created
    finally:
        db.close()
