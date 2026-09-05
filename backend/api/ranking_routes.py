from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.connection import get_db
from services.ranking_api_service import RankingApiService

router = APIRouter(prefix="/api/ranking", tags=["ranking"])


@router.get("/factors")
async def get_available_factors():
    return RankingApiService.available_factors()


@router.get("/table")
async def get_ranking_table(
    db: Session = Depends(get_db),
    days: int = Query(100, description="Number of days of historical data to use"),
    factors: Optional[str] = Query(None, description="Comma-separated list of factor IDs to compute"),
    limit: int = Query(50, description="Maximum number of cryptos to return"),
):
    return RankingApiService(db).ranking_table(days, factors, limit)


@router.get("/symbols")
async def get_available_symbols(
    db: Session = Depends(get_db),
    days: int = Query(100, description="Number of days to check for data availability"),
):
    return RankingApiService(db).available_symbols(days)
