from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import Account, AgentPeriodCheckpoint

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


def _to_float(x) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _to_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # Use a stable UTC representation for frontend parsing
    return dt.isoformat().replace("+00:00", "Z")


@router.get("/checkpoints/account/{account_id}")
def list_account_checkpoints(
    account_id: int,
    interval_seconds: int = Query(3600, description="Slice size in seconds"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    rows: List[AgentPeriodCheckpoint] = (
        db.query(AgentPeriodCheckpoint)
        .filter(
            AgentPeriodCheckpoint.account_id == account_id,
            AgentPeriodCheckpoint.interval_seconds == interval_seconds,
        )
        .order_by(AgentPeriodCheckpoint.period_end.desc())
        .limit(limit)
        .all()
    )

    return {
        "account_id": account_id,
        "account_name": account.name,
        "interval_seconds": interval_seconds,
        "items": [
            {
                "period_start": _to_iso_utc(r.period_start),
                "period_end": _to_iso_utc(r.period_end),
                "equity_start": _to_float(r.equity_start),
                "equity_end": _to_float(r.equity_end),
                "pnl": _to_float(r.pnl),
                "return_rate": r.return_rate,
                "created_at": _to_iso_utc(r.created_at),
            }
            for r in rows
        ],
    }


@router.get("/checkpoints/leaderboard")
def leaderboard(
    interval_seconds: int = Query(3600, description="Slice size in seconds"),
    period_end: Optional[datetime] = Query(None, description="Exact period_end; if omitted, use latest"),
    limit: int = Query(50, ge=1, le=500),
    order_by: str = Query("return", description="return|pnl"),
    db: Session = Depends(get_db),
):
    q = db.query(AgentPeriodCheckpoint).filter(AgentPeriodCheckpoint.interval_seconds == interval_seconds)

    if period_end is None:
        latest = (
            db.query(AgentPeriodCheckpoint.period_end)
            .filter(AgentPeriodCheckpoint.interval_seconds == interval_seconds)
            .order_by(AgentPeriodCheckpoint.period_end.desc())
            .first()
        )
        if not latest:
            return {"interval_seconds": interval_seconds, "period_end": None, "items": []}
        period_end = latest[0]

    q = q.filter(AgentPeriodCheckpoint.period_end == period_end)

    if order_by == "pnl":
        q = q.order_by(AgentPeriodCheckpoint.pnl.desc())
    else:
        q = q.order_by(AgentPeriodCheckpoint.return_rate.desc())

    rows = q.limit(limit).all()

    # Map account_id -> account info
    account_ids = [r.account_id for r in rows]
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all() if account_ids else []
    account_map: Dict[int, Account] = {a.id: a for a in accounts}

    return {
        "interval_seconds": interval_seconds,
        "period_end": _to_iso_utc(period_end),
        "order_by": order_by,
        "items": [
            {
                "account_id": r.account_id,
                "agent_name": account_map.get(r.account_id).name if account_map.get(r.account_id) else None,
                "agent_type": getattr(account_map.get(r.account_id), "agent_type", None) if account_map.get(r.account_id) else None,
                "equity_start": _to_float(r.equity_start),
                "equity_end": _to_float(r.equity_end),
                "pnl": _to_float(r.pnl),
                "return_rate": r.return_rate,
            }
            for r in rows
        ],
    }


@router.get("/checkpoints/compare")
def compare_agents(
    interval_seconds: int = Query(3600),
    start: Optional[datetime] = Query(None, description="period_end >= start"),
    end: Optional[datetime] = Query(None, description="period_end <= end"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    q = db.query(AgentPeriodCheckpoint).filter(AgentPeriodCheckpoint.interval_seconds == interval_seconds)
    if start is not None:
        q = q.filter(AgentPeriodCheckpoint.period_end >= start)
    if end is not None:
        q = q.filter(AgentPeriodCheckpoint.period_end <= end)

    rows = q.order_by(AgentPeriodCheckpoint.period_end.desc()).limit(limit).all()
    if not rows:
        return {"interval_seconds": interval_seconds, "items": []}

    account_ids = sorted({r.account_id for r in rows})
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
    account_map: Dict[int, Account] = {a.id: a for a in accounts}

    return {
        "interval_seconds": interval_seconds,
        "items": [
            {
                "account_id": r.account_id,
                "agent_name": account_map.get(r.account_id).name if account_map.get(r.account_id) else None,
                "period_start": _to_iso_utc(r.period_start),
                "period_end": _to_iso_utc(r.period_end),
                "pnl": _to_float(r.pnl),
                "return_rate": r.return_rate,
            }
            for r in rows
        ],
    }
