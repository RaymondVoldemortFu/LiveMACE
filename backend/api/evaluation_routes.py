from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.connection import get_db
from services.evaluation_api_service import (
    AccountNotFoundError,
    EvaluationApiService,
    InvalidLeaderboardOrderError,
)

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


@router.get("/checkpoints/account/{account_id}")
def list_account_checkpoints(
    account_id: int,
    interval_seconds: int = Query(3600, description="Slice size in seconds"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    try:
        return EvaluationApiService(db).list_account_checkpoints(account_id, interval_seconds, limit)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/checkpoints/leaderboard")
def leaderboard(
    interval_seconds: int = Query(3600, description="Slice size in seconds"),
    period_end: Optional[datetime] = Query(None, description="Exact period_end; if omitted, use latest"),
    limit: int = Query(50, ge=1, le=500),
    order_by: str = Query("return", description="return|pnl|volatility"),
    db: Session = Depends(get_db),
):
    try:
        return EvaluationApiService(db).leaderboard(interval_seconds, period_end, limit, order_by)
    except InvalidLeaderboardOrderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/checkpoints/compare")
def compare_agents(
    interval_seconds: int = Query(3600),
    start: Optional[datetime] = Query(None, description="period_end >= start"),
    end: Optional[datetime] = Query(None, description="period_end <= end"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    return EvaluationApiService(db).compare_agents(interval_seconds, start, end, limit)
