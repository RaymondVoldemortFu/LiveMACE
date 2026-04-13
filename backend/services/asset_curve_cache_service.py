from __future__ import annotations

from typing import Dict, List, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from database.connection import SessionLocal
from database.models import AssetCurveSnapshot
from services.asset_curve_calculator import get_all_asset_curves_data_new
from services.time_source import now_utc


VALID_ASSET_CURVE_TIMEFRAMES = {"5m", "1h", "1d"}

ASSET_CURVE_MAX_POINTS = {
    "5m": 20,
    "1h": 20,
    "1d": 31,
}

# Higher precision curves refresh more frequently.
ASSET_CURVE_CACHE_REFRESH_SECONDS = {
    "5m": 60,
    "1h": 300,
    "1d": 3600,
}


def _validate_timeframe(timeframe: str) -> str:
    normalized = (timeframe or "").strip().lower()
    if normalized not in VALID_ASSET_CURVE_TIMEFRAMES:
        raise ValueError(f"Invalid timeframe: {timeframe}")
    return normalized


def get_curve_point_limit(timeframe: str) -> int:
    normalized = _validate_timeframe(timeframe)
    return ASSET_CURVE_MAX_POINTS[normalized]


def should_backfill_recent_1h(max_timestamp: int | None, now_ts: int) -> bool:
    if max_timestamp is None:
        return True
    return int(max_timestamp) < int(now_ts) - 3600


def refresh_asset_curve_cache(db: Session, timeframe: str) -> int:
    normalized = _validate_timeframe(timeframe)
    point_limit = get_curve_point_limit(normalized)
    points = get_all_asset_curves_data_new(db, normalized, points=point_limit)
    if not points:
        return 0

    account_ids = set()
    timestamps = set()
    for point in points:
        account_id = int(point["account_id"])
        timestamp = int(point["timestamp"])
        account_ids.add(account_id)
        timestamps.add(timestamp)

    existing_rows = (
        db.query(AssetCurveSnapshot)
        .filter(
            AssetCurveSnapshot.timeframe == normalized,
            AssetCurveSnapshot.account_id.in_(list(account_ids)),
            AssetCurveSnapshot.timestamp.in_(list(timestamps)),
        )
        .all()
    )
    existing_map: Dict[Tuple[int, int], AssetCurveSnapshot] = {
        (int(row.account_id), int(row.timestamp)): row for row in existing_rows
    }

    for point in points:
        account_id = int(point["account_id"])
        timestamp = int(point["timestamp"])
        row = existing_map.get((account_id, timestamp))
        if row is None:
            row = AssetCurveSnapshot(
                account_id=account_id,
                timeframe=normalized,
                timestamp=timestamp,
            )
            db.add(row)

        row.datetime_str = str(point["datetime_str"])
        row.user_id = int(point["user_id"])
        row.username = str(point["username"])
        row.total_assets = float(point["total_assets"])
        row.initial_capital = float(point["initial_capital"])
        row.profit = float(point["profit"])
        row.profit_percentage = float(point["profit_percentage"])
        row.cash = float(point["cash"])
        row.positions_value = float(point["positions_value"])

    db.commit()
    return len(points)


def get_asset_curve_cache(db: Session, timeframe: str, limit_timestamps: int = 20) -> List[dict]:
    normalized = _validate_timeframe(timeframe)
    if limit_timestamps <= 0:
        return []

    ts_rows = (
        db.query(AssetCurveSnapshot.timestamp)
        .filter(AssetCurveSnapshot.timeframe == normalized)
        .distinct()
        .order_by(AssetCurveSnapshot.timestamp.desc())
        .limit(limit_timestamps)
        .all()
    )
    timestamps = [int(row[0]) for row in ts_rows]
    if not timestamps:
        return []

    timestamps_asc = sorted(timestamps)
    rows = (
        db.query(AssetCurveSnapshot)
        .filter(
            AssetCurveSnapshot.timeframe == normalized,
            AssetCurveSnapshot.timestamp.in_(timestamps_asc),
        )
        .order_by(AssetCurveSnapshot.timestamp.asc(), AssetCurveSnapshot.account_id.asc())
        .all()
    )

    return [
        {
            "timestamp": int(row.timestamp),
            "datetime_str": row.datetime_str,
            "account_id": int(row.account_id),
            "user_id": int(row.user_id),
            "username": row.username,
            "total_assets": float(row.total_assets),
            "initial_capital": float(row.initial_capital),
            "profit": float(row.profit),
            "profit_percentage": float(row.profit_percentage),
            "cash": float(row.cash),
            "positions_value": float(row.positions_value),
        }
        for row in rows
    ]


def refresh_asset_curve_cache_job(timeframe: str) -> None:
    db = SessionLocal()
    try:
        refresh_asset_curve_cache(db, timeframe)
    finally:
        db.close()


def backfill_recent_1h_curve_on_startup() -> int:
    """Startup self-healing: backfill when no 1h curve exists in last hour."""
    db = SessionLocal()
    try:
        max_ts = (
            db.query(func.max(AssetCurveSnapshot.timestamp))
            .filter(AssetCurveSnapshot.timeframe == "1h")
            .scalar()
        )
        now_ts = int(now_utc().timestamp())
        if not should_backfill_recent_1h(max_ts, now_ts):
            return 0
        return refresh_asset_curve_cache(db, "1h")
    finally:
        db.close()
