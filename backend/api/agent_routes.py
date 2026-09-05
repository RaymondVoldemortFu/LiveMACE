from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database.connection import get_db
from services.agent_api_service import AccountNotFoundError, AgentApiService, TraceNotFoundError

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/trace/{trace_id}")
def get_agent_trace(trace_id: str, db: Session = Depends(get_db)):
    try:
        return AgentApiService(db).get_trace(trace_id)
    except TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/latest/{account_id}")
def get_latest_trace(account_id: int, db: Session = Depends(get_db)):
    try:
        return AgentApiService(db).get_latest_trace(account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/history/{account_id}")
def get_trace_history(account_id: int, limit: int = 20, db: Session = Depends(get_db)):
    return AgentApiService(db).get_trace_history(account_id, limit)
