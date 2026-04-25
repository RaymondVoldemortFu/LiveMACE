#!/usr/bin/env python3
"""
Repair asset curve snapshot data for a specified account in a SQLite database.

Use agent_period_checkpoints (1h) as the source of truth to rebuild:
- 1h snapshots
- 1d snapshots (latest checkpoint per UTC day)

Optionally purge 5m snapshots for the account to remove known-bad historical cache rows.

Example:
  python script/repair_account_curve_in_sqlite.py \
    --db-path ../alpha_arena.sqlite \
    --account-name gemini-3.1-pro-preview-react-tool \
    --purge-5m
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class AccountRow:
    id: int
    user_id: int
    username: str
    name: str
    initial_capital: float


def _to_utc_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolve_db_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists() or not path.is_file():
        raise SystemExit(f"SQLite file not found: {path}")
    return path


def _find_account(
    conn: sqlite3.Connection,
    account_id: Optional[int],
    account_name: Optional[str],
) -> AccountRow:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if account_id is not None:
        row = cur.execute(
            """
            SELECT a.id, a.user_id, u.username, a.name, a.initial_capital
            FROM accounts a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.id = ?
            """,
            (account_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"Account id={account_id} not found")
        return AccountRow(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            username=str(row["username"] or "unknown"),
            name=str(row["name"]),
            initial_capital=float(row["initial_capital"] or 0.0),
        )

    if not account_name:
        raise SystemExit("Either --account-id or --account-name is required")

    rows = cur.execute(
        """
        SELECT a.id, a.user_id, u.username, a.name, a.initial_capital
        FROM accounts a
        LEFT JOIN users u ON u.id = a.user_id
        WHERE a.name = ?
        ORDER BY a.id ASC
        """,
        (account_name,),
    ).fetchall()

    if not rows:
        raise SystemExit(f"Account name not found: {account_name}")
    if len(rows) > 1:
        ids = ", ".join(str(r["id"]) for r in rows)
        raise SystemExit(f"Multiple accounts matched name '{account_name}', please use --account-id: {ids}")

    row = rows[0]
    return AccountRow(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        username=str(row["username"] or "unknown"),
        name=str(row["name"]),
        initial_capital=float(row["initial_capital"] or 0.0),
    )


def _load_hourly_checkpoints(conn: sqlite3.Connection, account_id: int) -> List[Tuple[datetime, float]]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT period_end, equity_end
        FROM agent_period_checkpoints
        WHERE account_id = ? AND interval_seconds = 3600
        ORDER BY period_end ASC
        """,
        (account_id,),
    ).fetchall()

    result: List[Tuple[datetime, float]] = []
    for row in rows:
        pe = row["period_end"]
        eq = row["equity_end"]
        if pe is None or eq is None:
            continue
        result.append((_to_utc_datetime(pe), float(eq)))
    return result


def _build_daily_points(hourly_points: Iterable[Tuple[datetime, float]]) -> List[Tuple[datetime, float]]:
    latest_per_day: Dict[str, Tuple[datetime, float]] = {}
    for dt, equity in hourly_points:
        day_key = dt.date().isoformat()
        latest_per_day[day_key] = (dt, equity)
    return [latest_per_day[k] for k in sorted(latest_per_day.keys())]


def _upsert_snapshot(
    cur: sqlite3.Cursor,
    account: AccountRow,
    timeframe: str,
    dt: datetime,
    equity: float,
) -> None:
    ts = int(dt.timestamp())
    profit = equity - account.initial_capital
    profit_percentage = (profit / account.initial_capital * 100.0) if account.initial_capital > 0 else 0.0

    cur.execute(
        """
        INSERT INTO asset_curve_snapshots (
            account_id, timeframe, timestamp, datetime_str,
            user_id, username,
            total_assets, initial_capital, profit, profit_percentage,
            cash, positions_value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, timeframe, timestamp) DO UPDATE SET
            datetime_str=excluded.datetime_str,
            user_id=excluded.user_id,
            username=excluded.username,
            total_assets=excluded.total_assets,
            initial_capital=excluded.initial_capital,
            profit=excluded.profit,
            profit_percentage=excluded.profit_percentage,
            cash=excluded.cash,
            positions_value=excluded.positions_value,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            account.id,
            timeframe,
            ts,
            dt.isoformat(),
            account.user_id,
            account.name,
            equity,
            account.initial_capital,
            profit,
            profit_percentage,
            0.0,
            0.0,
        ),
    )


def _delete_timeframe(cur: sqlite3.Cursor, account_id: int, timeframe: str) -> int:
    cur.execute(
        "DELETE FROM asset_curve_snapshots WHERE account_id = ? AND timeframe = ?",
        (account_id, timeframe),
    )
    return int(cur.rowcount or 0)


def _count_timeframe_rows(cur: sqlite3.Cursor, account_id: int, timeframe: str) -> int:
    row = cur.execute(
        "SELECT COUNT(*) FROM asset_curve_snapshots WHERE account_id = ? AND timeframe = ?",
        (account_id, timeframe),
    ).fetchone()
    return int(row[0] if row else 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair account asset curve snapshots in SQLite")
    parser.add_argument(
        "--db-path",
        default="../alpha_arena.sqlite",
        help="Path to sqlite file (default: ../alpha_arena.sqlite, when run from backend)",
    )
    parser.add_argument("--account-id", type=int, default=None, help="Target account id")
    parser.add_argument("--account-name", default=None, help="Target account name")
    parser.add_argument(
        "--purge-5m",
        action="store_true",
        help="Also delete 5m snapshots for this account (recommended when data is known-bad)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without committing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    backend_dir = Path(__file__).resolve().parent.parent
    db_path = _resolve_db_path(str((backend_dir / args.db_path).resolve()) if not Path(args.db_path).is_absolute() else args.db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        account = _find_account(conn, args.account_id, args.account_name)
        checkpoints = _load_hourly_checkpoints(conn, account.id)
        if not checkpoints:
            raise SystemExit(
                f"No hourly checkpoints found for account id={account.id} name={account.name}; cannot rebuild 1h/1d"
            )

        daily_points = _build_daily_points(checkpoints)

        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        before_1h = _count_timeframe_rows(cur, account.id, "1h")
        before_1d = _count_timeframe_rows(cur, account.id, "1d")
        before_5m = _count_timeframe_rows(cur, account.id, "5m")

        deleted_1h = _delete_timeframe(cur, account.id, "1h")
        deleted_1d = _delete_timeframe(cur, account.id, "1d")
        deleted_5m = _delete_timeframe(cur, account.id, "5m") if args.purge_5m else 0

        for dt, equity in checkpoints:
            _upsert_snapshot(cur, account, "1h", dt, equity)

        for dt, equity in daily_points:
            _upsert_snapshot(cur, account, "1d", dt, equity)

        after_1h = _count_timeframe_rows(cur, account.id, "1h")
        after_1d = _count_timeframe_rows(cur, account.id, "1d")
        after_5m = _count_timeframe_rows(cur, account.id, "5m")

        print(f"[ACCOUNT] id={account.id} name={account.name}")
        print(f"[DB] {db_path}")
        print(f"[CHECKPOINTS] hourly={len(checkpoints)} daily={len(daily_points)}")
        print(f"[1h] before={before_1h} deleted={deleted_1h} after={after_1h}")
        print(f"[1d] before={before_1d} deleted={deleted_1d} after={after_1d}")
        print(f"[5m] before={before_5m} deleted={deleted_5m} after={after_5m}")

        if args.dry_run:
            conn.rollback()
            print("[DRY-RUN] rollback complete; no data was changed")
        else:
            conn.commit()
            print("[DONE] snapshot repair committed")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
