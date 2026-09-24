from schemas.domain_reads import LatestTrace, ManualDecisionResponse
from schemas.control_plane import AgentTrace, TraceSummary
from fastapi import Header
from pydantic import BaseModel, Field
import os
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.connection import get_db
from services.agent_api_service import (
    AccountNotFoundError,
    AgentApiService,
    TraceNotFoundError,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/trace/{trace_id}", response_model=AgentTrace)
def get_agent_trace(trace_id: str, db: Session = Depends(get_db)):
    try:
        return AgentApiService(db).get_trace(trace_id)
    except TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/latest/{account_id}", response_model=LatestTrace, response_model_exclude_unset=True)
def get_latest_trace(account_id: int, db: Session = Depends(get_db)):
    try:
        return AgentApiService(db).get_latest_trace(account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/history/{account_id}", response_model=list[TraceSummary])
def get_trace_history(account_id: int, limit: int = 20, db: Session = Depends(get_db)):
    return AgentApiService(db).get_trace_history(account_id, limit)


# Operator requests execute inside the scheduler process, sharing its round lock
# and sandbox pool. The endpoint is disabled unless an operator secret is set.


class ManualDecisionRequest(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=20)
    max_concurrency: int = Field(default=1, ge=1, le=4)


@router.post("/round", response_model=ManualDecisionResponse, response_model_exclude_unset=True)
def run_manual_decision_round(
    request: ManualDecisionRequest,
    x_operator_token: str = Header(default=""),
):
    expected = os.getenv("DECISION_OPERATOR_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=404, detail="Manual decisions are disabled")
    if not secrets.compare_digest(expected, x_operator_token):
        raise HTTPException(status_code=403, detail="Invalid operator token")
    if any(account_id <= 0 for account_id in request.account_ids):
        raise HTTPException(status_code=422, detail="account_ids must be positive")
    from benchmark.application.decisions.service import (
        DecisionRoundService,
        RunDecisionRound,
    )
    from benchmark.application.decisions.selection import select_manual_account_ids

    try:
        result = DecisionRoundService(selector=select_manual_account_ids).run(
            RunDecisionRound(
                tuple(request.account_ids), request.max_concurrency, "operator"
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.decision_round_id is None:
        raise HTTPException(
            status_code=409, detail="A decision round is already running"
        )
    return result
