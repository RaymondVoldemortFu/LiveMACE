import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.connection import get_db
from benchmark.application.compliance.service import ComplianceRequest, ComplianceService, ComplianceResultDTO

from schemas.compliance import ComplianceHistory, ComplianceTrend, ComplianceStats, RecentDecisions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/compliance", tags=["compliance"])


def _execute(operation, message: str):
    try:
        return operation()
    except Exception as exc:
        logger.error("%s: %s", message, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{message}: {exc}") from exc


@router.get("/account/{account_id}/history", response_model=ComplianceHistory)
async def get_compliance_history(
    account_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return _execute(
        lambda: ComplianceService(db).history(account_id, limit, offset),
        "Failed to get compliance history",
    )


@router.get("/account/{account_id}/trend", response_model=ComplianceTrend)
async def get_compliance_trend(
    account_id: int,
    period: str = Query("day", pattern="^(day|week|month)$"),
    metric: str = Query("final_score", pattern="^(gate_pass_rate|final_score|s_rule_sat|s_audit)$"),
    db: Session = Depends(get_db),
):
    return _execute(
        lambda: ComplianceService(db).trend(account_id, period, metric),
        "Failed to get compliance trend",
    )


@router.get("/account/{account_id}/stats", response_model=ComplianceStats)
async def get_compliance_stats(account_id: int, db: Session = Depends(get_db)):
    return _execute(
        lambda: ComplianceService(db).stats(account_id),
        "Failed to get compliance stats",
    )


@router.get("/account/{account_id}/evaluation", response_model=ComplianceResultDTO)
async def evaluate_compliance(
    account_id: int,
    trace_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    result = ComplianceService(db).evaluate(ComplianceRequest(account_id=account_id, trace_id=trace_id))
    return result.model_dump()


@router.get("/recent-decisions", response_model=RecentDecisions)
async def get_recent_decisions(
    account_id: int = Query(..., description="Account ID"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return _execute(
        lambda: ComplianceService(db).recent_decisions(account_id, limit),
        "Failed to get recent decisions",
    )
