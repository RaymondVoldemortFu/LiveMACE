from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.connection import get_db
from services.memory_api_service import MemoryApiService

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/{account_id}/list")
def get_memories(account_id: int, market: Optional[str] = Query(None), db: Session = Depends(get_db)):
    return MemoryApiService(db).list_memories(account_id, market)


@router.get("/{account_id}/metrics")
def get_metrics(account_id: int, market: Optional[str] = Query(None), db: Session = Depends(get_db)):
    try:
        return MemoryApiService(db).get_metrics(account_id, market)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{account_id}/growth-timeline")
def get_growth_timeline(account_id: int, market: Optional[str] = Query(None), db: Session = Depends(get_db)):
    return MemoryApiService(db).get_growth_timeline(account_id, market)


@router.delete("/clear-all")
def clear_all_memories(db: Session = Depends(get_db)):
    try:
        return MemoryApiService(db).clear_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{account_id}/clear")
def clear_memories(account_id: int, db: Session = Depends(get_db)):
    try:
        return MemoryApiService(db).clear_account(account_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
