from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.connection import get_db
from services.evaluation_api_service import (
    AccountNotFoundError,
    EvaluationApiService,
    InvalidLeaderboardOrderError,
)

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


class AccountCheckpointItem(BaseModel):
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    equity_start: Optional[float] = None
    equity_end: Optional[float] = None
    pnl: Optional[float] = None
    return_rate: float
    volatility: Optional[float] = None
    created_at: Optional[str] = None


class AccountCheckpointsResponse(BaseModel):
    account_id: int
    account_name: str
    interval_seconds: int
    items: list[AccountCheckpointItem]


class LeaderboardItem(BaseModel):
    account_id: int
    agent_name: Optional[str] = None
    agent_type: Optional[str] = None
    equity_start: Optional[float] = None
    equity_end: Optional[float] = None
    pnl: Optional[float] = None
    return_rate: float
    volatility: Optional[float] = None


class LeaderboardResponse(BaseModel):
    interval_seconds: int
    period_end: Optional[str] = None
    order_by: Optional[str] = None
    items: list[LeaderboardItem] = []


class CompareItem(BaseModel):
    account_id: int
    agent_name: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    pnl: Optional[float] = None
    return_rate: float
    volatility: Optional[float] = None


class CompareResponse(BaseModel):
    interval_seconds: int
    items: list[CompareItem] = []


@router.get("/checkpoints/account/{account_id}", response_model=AccountCheckpointsResponse)
def list_account_checkpoints(
    account_id: int,
    interval_seconds: int = Query(3600, description="Slice size in seconds"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    try:
        payload = EvaluationApiService(db).list_account_checkpoints(account_id, interval_seconds, limit)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AccountCheckpointsResponse.model_validate(payload).model_dump()


@router.get("/checkpoints/leaderboard", response_model=LeaderboardResponse, response_model_exclude_unset=True)
def leaderboard(
    interval_seconds: int = Query(3600, description="Slice size in seconds"),
    period_end: Optional[datetime] = Query(None, description="Exact period_end; if omitted, use latest"),
    limit: int = Query(50, ge=1, le=500),
    order_by: str = Query("return", description="return|pnl|volatility"),
    db: Session = Depends(get_db),
):
    try:
        payload = EvaluationApiService(db).leaderboard(interval_seconds, period_end, limit, order_by)
    except InvalidLeaderboardOrderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LeaderboardResponse.model_validate(payload).model_dump(exclude_unset=True)


@router.get("/checkpoints/compare", response_model=CompareResponse)
def compare_agents(
    interval_seconds: int = Query(3600),
    start: Optional[datetime] = Query(None, description="period_end >= start"),
    end: Optional[datetime] = Query(None, description="period_end <= end"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    payload = EvaluationApiService(db).compare_agents(interval_seconds, start, end, limit)
    return CompareResponse.model_validate(payload).model_dump()
