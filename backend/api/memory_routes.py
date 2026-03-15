from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from collections import defaultdict
from typing import Optional

from database.connection import get_db
from database.models import AgentMemory
from services.evaluation.memory_evaluator import MemoryEvaluator

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/{account_id}/list")
def get_memories(account_id: int, market: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get all memories for an account, optionally filtered by market"""
    query = db.query(AgentMemory).filter(
        AgentMemory.account_id == account_id
    )
    if market:
        query = query.filter(AgentMemory.market == market)
    memories = query.order_by(AgentMemory.created_at.desc()).all()

    return {
        "memories": [
            {
                "id": m.id,
                "content": m.content,
                "market": m.market,
                "created_at": m.created_at,
                "retrieval_count": m.retrieval_count or 0,
                "last_retrieved_at": m.last_retrieved_at
            }
            for m in memories
        ]
    }


@router.get("/{account_id}/metrics")
def get_metrics(account_id: int, market: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get 4 core memory metrics, optionally filtered by market"""
    try:
        evaluator = MemoryEvaluator(db)
        agent_data = {"account_id": account_id}
        if market:
            agent_data["market"] = market
        result = evaluator.evaluate(agent_data=agent_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{account_id}/growth-timeline")
def get_growth_timeline(account_id: int, market: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get time-series data for growth curve, optionally filtered by market"""
    query = db.query(AgentMemory).filter(
        AgentMemory.account_id == account_id
    )
    if market:
        query = query.filter(AgentMemory.market == market)
    memories = query.order_by(AgentMemory.created_at).all()

    if not memories:
        return {"timeline": []}

    # Group by date
    daily_counts = defaultdict(int)
    for m in memories:
        date = m.created_at.date().isoformat()
        daily_counts[date] += 1

    # Calculate cumulative
    timeline = []
    cumulative = 0
    for date in sorted(daily_counts.keys()):
        cumulative += daily_counts[date]
        timeline.append({
            "date": date,
            "cumulative_count": cumulative,
            "daily_additions": daily_counts[date]
        })

    return {"timeline": timeline}
