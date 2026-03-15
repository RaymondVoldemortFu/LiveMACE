"""Snapshot-related Pydantic schemas for API validation"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class AccountSnapshotOut(BaseModel):
    """Account snapshot output"""
    id: int
    account_id: int
    ts: datetime
    total_equity: float
    cash: float
    positions_value: float

    class Config:
        from_attributes = True


class AccountSnapshotCreate(BaseModel):
    """Create account snapshot (admin only)"""
    account_id: int
    total_equity: float
    cash: float
    positions_value: float


class SnapshotTimeRange(BaseModel):
    """Query snapshots in time range"""
    account_id: int
    start_time: datetime
    end_time: datetime


class EquityCurvePoint(BaseModel):
    """Single point in equity curve"""
    timestamp: datetime
    equity: float
    cash: float
    positions_value: float


class EquityCurveResponse(BaseModel):
    """Equity curve with metrics"""
    account_id: int
    account_name: str
    start_time: datetime
    end_time: datetime
    data_points: list[EquityCurvePoint]
    metrics: dict  # Contains volatility, sharpe, max_drawdown, etc.
