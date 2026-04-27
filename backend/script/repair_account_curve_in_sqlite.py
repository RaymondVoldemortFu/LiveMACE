#!/usr/bin/env python3
"""
Repair asset curve snapshots for a specified account.

Supports both:
- MySQL (default, from backend/.env DATABASE_URL)
- SQLite (via --db-path or --database-url)

Repair strategy:
- Rebuild 1h snapshots from agent_period_checkpoints (interval_seconds=3600)
- Rebuild 1d snapshots from latest hourly checkpoint per UTC day
- Optional purge of 5m snapshots for the account
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Dict, Iterable, List, Optional, Tuple

import dotenv


@dataclass
class AccountRow:
    id: int
    user_id: int
    username: str
    name: str
    initial_capital: float


def _to_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_sqlite_url(raw_path: str) -> str:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists() or not path.is_file():
        raise SystemExit(f"SQLite file not found: {path}")
    path_posix = path.as_posix()
    return f"sqlite:///{path_posix}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair account asset curve snapshots in DB")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL directly (mysql+pymysql://... or sqlite:///...)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite file path shortcut; converted to sqlite:/// URL automatically",
    )
    parser.add_argument("--account-id", type=int, default=None, help="Target account id")
    parser.add_argument("--account-name", default=None, help="Target account name")
    parser.add_argument(
        "--purge-5m",
        action="store_true",
        help="Also delete 5m snapshots for this account",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without committing",
    )
    return parser.parse_args()


def _configure_database_url(args: argparse.Namespace) -> str:
    if args.database_url and args.db_path:
        raise SystemExit("Use either --database-url or --db-path, not both")

    if args.database_url:
        os.environ["DATABASE_URL"] = str(args.database_url).strip()
    elif args.db_path:
        os.environ["DATABASE_URL"] = _resolve_sqlite_url(args.db_path)

    configured = (os.getenv("DATABASE_URL") or "").strip()
    if not configured:
        raise SystemExit("DATABASE_URL is empty; set it in env or pass --database-url/--db-path")
    return configured


def _mask_database_url(database_url: str) -> str:
    raw = (database_url or "").strip()
    if not raw:
        return raw
    try:
        parts = urlsplit(raw)
        if "@" not in parts.netloc:
            return raw
        auth, host = parts.netloc.rsplit("@", 1)
        if ":" in auth:
            user, _ = auth.split(":", 1)
            masked_auth = f"{user}:***"
        else:
            masked_auth = auth
        return urlunsplit((parts.scheme, f"{masked_auth}@{host}", parts.path, parts.query, parts.fragment))
    except Exception:
        return "***"


def _find_account(
    db,
    Account,
    User,
    account_id: Optional[int],
    account_name: Optional[str],
) -> AccountRow:
    if account_id is not None:
        row = (
            db.query(Account.id, Account.user_id, User.username, Account.name, Account.initial_capital)
            .outerjoin(User, User.id == Account.user_id)
            .filter(Account.id == account_id)
            .first()
        )
        if not row:
            raise SystemExit(f"Account id={account_id} not found")
        return AccountRow(
            id=int(row.id),
            user_id=int(row.user_id),
            username=str(row.username or "unknown"),
            name=str(row.name),
            initial_capital=float(row.initial_capital or 0.0),
        )

    if not account_name:
        raise SystemExit("Either --account-id or --account-name is required")

    rows = (
        db.query(Account.id, Account.user_id, User.username, Account.name, Account.initial_capital)
        .outerjoin(User, User.id == Account.user_id)
        .filter(Account.name == account_name)
        .order_by(Account.id.asc())
        .all()
    )

    if not rows:
        raise SystemExit(f"Account name not found: {account_name}")
    if len(rows) > 1:
        ids = ", ".join(str(r.id) for r in rows)
        raise SystemExit(f"Multiple accounts matched name '{account_name}', please use --account-id: {ids}")

    row = rows[0]
    return AccountRow(
        id=int(row.id),
        user_id=int(row.user_id),
        username=str(row.username or "unknown"),
        name=str(row.name),
        initial_capital=float(row.initial_capital or 0.0),
    )


def _load_hourly_checkpoints(db, AgentPeriodCheckpoint, account_id: int) -> List[Tuple[datetime, float]]:
    rows = (
        db.query(AgentPeriodCheckpoint.period_end, AgentPeriodCheckpoint.equity_end)
        .filter(
            AgentPeriodCheckpoint.account_id == account_id,
            AgentPeriodCheckpoint.interval_seconds == 3600,
        )
        .order_by(AgentPeriodCheckpoint.period_end.asc())
        .all()
    )

    result: List[Tuple[datetime, float]] = []
    for period_end, equity_end in rows:
        if period_end is None or equity_end is None:
            continue
        result.append((_to_utc_datetime(period_end), float(equity_end)))
    return result


def _build_daily_points(hourly_points: Iterable[Tuple[datetime, float]]) -> List[Tuple[datetime, float]]:
    latest_per_day: Dict[str, Tuple[datetime, float]] = {}
    for dt, equity in hourly_points:
        day_key = dt.date().isoformat()
        latest_per_day[day_key] = (dt, equity)
    return [latest_per_day[k] for k in sorted(latest_per_day.keys())]


def _count_timeframe_rows(db, AssetCurveSnapshot, account_id: int, timeframe: str) -> int:
    return int(
        db.query(AssetCurveSnapshot)
        .filter(AssetCurveSnapshot.account_id == account_id, AssetCurveSnapshot.timeframe == timeframe)
        .count()
    )


def _delete_timeframe_rows(db, AssetCurveSnapshot, account_id: int, timeframe: str) -> int:
    return int(
        db.query(AssetCurveSnapshot)
        .filter(AssetCurveSnapshot.account_id == account_id, AssetCurveSnapshot.timeframe == timeframe)
        .delete(synchronize_session=False)
    )


def _insert_snapshot_rows(
    db,
    AssetCurveSnapshot,
    account: AccountRow,
    timeframe: str,
    points: Iterable[Tuple[datetime, float]],
) -> None:
    for dt, equity in points:
        ts = int(dt.timestamp())
        profit = equity - account.initial_capital
        profit_percentage = (profit / account.initial_capital * 100.0) if account.initial_capital > 0 else 0.0

        row = AssetCurveSnapshot(
            account_id=account.id,
            timeframe=timeframe,
            timestamp=ts,
            datetime_str=dt.isoformat(),
            user_id=account.user_id,
            username=account.username,
            total_assets=equity,
            initial_capital=account.initial_capital,
            profit=profit,
            profit_percentage=profit_percentage,
            cash=0.0,
            positions_value=0.0,
        )
        db.add(row)


def main() -> int:
    args = parse_args()

    backend_dir = Path(__file__).resolve().parent.parent
    os.chdir(backend_dir)

    # Load backend/.env so DATABASE_URL can be picked up without extra flags.
    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True), override=False)

    configured_db_url = _configure_database_url(args)

    # Import after DATABASE_URL is finalized.
    from database.connection import SessionLocal
    from database.models import Account, User, AgentPeriodCheckpoint, AssetCurveSnapshot

    db = SessionLocal()
    try:
        account = _find_account(db, Account, User, args.account_id, args.account_name)
        checkpoints = _load_hourly_checkpoints(db, AgentPeriodCheckpoint, account.id)
        if not checkpoints:
            raise SystemExit(
                f"No hourly checkpoints found for account id={account.id} name={account.name}; cannot rebuild 1h/1d"
            )

        daily_points = _build_daily_points(checkpoints)

        before_1h = _count_timeframe_rows(db, AssetCurveSnapshot, account.id, "1h")
        before_1d = _count_timeframe_rows(db, AssetCurveSnapshot, account.id, "1d")
        before_5m = _count_timeframe_rows(db, AssetCurveSnapshot, account.id, "5m")

        deleted_1h = _delete_timeframe_rows(db, AssetCurveSnapshot, account.id, "1h")
        deleted_1d = _delete_timeframe_rows(db, AssetCurveSnapshot, account.id, "1d")
        deleted_5m = (
            _delete_timeframe_rows(db, AssetCurveSnapshot, account.id, "5m") if args.purge_5m else 0
        )

        _insert_snapshot_rows(db, AssetCurveSnapshot, account, "1h", checkpoints)
        _insert_snapshot_rows(db, AssetCurveSnapshot, account, "1d", daily_points)
        db.flush()

        after_1h = _count_timeframe_rows(db, AssetCurveSnapshot, account.id, "1h")
        after_1d = _count_timeframe_rows(db, AssetCurveSnapshot, account.id, "1d")
        after_5m = _count_timeframe_rows(db, AssetCurveSnapshot, account.id, "5m")

        print(f"[ACCOUNT] id={account.id} name={account.name}")
        print(f"[DATABASE_URL] {_mask_database_url(configured_db_url)}")
        print(f"[CHECKPOINTS] hourly={len(checkpoints)} daily={len(daily_points)}")
        print(f"[1h] before={before_1h} deleted={deleted_1h} after={after_1h}")
        print(f"[1d] before={before_1d} deleted={deleted_1d} after={after_1d}")
        print(f"[5m] before={before_5m} deleted={deleted_5m} after={after_5m}")

        if args.dry_run:
            db.rollback()
            print("[DRY-RUN] rollback complete; no data was changed")
        else:
            db.commit()
            print("[DONE] snapshot repair committed")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
