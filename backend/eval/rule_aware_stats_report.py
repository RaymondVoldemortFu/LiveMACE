"""
Rule-Aware Agent Offline Stats Report
=====================================
Read Rule-Aware statistics directly from a SQLite database and generate:
1) Console summary
2) CSV/JSON data exports
3) Trend charts (PNG)

Usage examples:
  conda run -n uvbench python eval/rule_aware_stats_report.py --db-path ./alpha_arena.sqlite
  conda run -n uvbench python eval/rule_aware_stats_report.py --db-path ./alpha_arena.sqlite --days 14
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
except Exception:  # pragma: no cover
    plt = None
    mdates = None


@dataclass
class AccountInfo:
    id: int
    name: str
    enable_rule_aware: Optional[str]
    agent_type: Optional[str]
    initial_capital: Optional[float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rule_aware_stats_report",
        description="Generate offline Rule-Aware stats report from SQLite DB.",
    )
    parser.add_argument(
        "--db-path",
        default="./alpha_arena.sqlite",
        help="Path to SQLite database file (default: ./alpha_arena.sqlite)",
    )
    parser.add_argument(
        "--output-dir",
        default="./eval_outputs/rule_aware_stats",
        help=(
            "Output root directory. The script creates a timestamped subfolder "
            "for each run (default root: ./eval_outputs/rule_aware_stats)."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only include records in the recent N days for trend charts (default: 30)",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        action="append",
        default=None,
        help="Restrict to one or more account IDs; can be used multiple times.",
    )
    parser.add_argument(
        "--no-charts",
        action="store_true",
        help="Skip PNG chart generation.",
    )
    return parser.parse_args()


def connect_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(r[1]) for r in rows]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    txt = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            dt = datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_to_five_minutes(ts: datetime) -> datetime:
    minute_rounded = int(round(ts.minute / 5.0) * 5)
    if minute_rounded == 60:
        ts = ts.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        ts = ts.replace(minute=minute_rounded, second=0, microsecond=0)
    return ts


def latest_row_datetime(rows: Sequence[sqlite3.Row], key: str) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for row in rows:
        ts = parse_ts(row[key])
        if ts is None:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def pick_rule_aware_accounts(
    conn: sqlite3.Connection,
    account_ids: Optional[Sequence[int]],
) -> List[AccountInfo]:
    cols = get_columns(conn, "accounts")
    has_enable = "enable_rule_aware" in cols
    has_agent_type = "agent_type" in cols
    has_initial_capital = "initial_capital" in cols

    query = "SELECT id, name"
    if has_enable:
        query += ", enable_rule_aware"
    else:
        query += ", NULL AS enable_rule_aware"
    if has_agent_type:
        query += ", agent_type"
    else:
        query += ", NULL AS agent_type"
    if has_initial_capital:
        query += ", initial_capital"
    else:
        query += ", NULL AS initial_capital"
    query += " FROM accounts"

    conditions: List[str] = []
    params: List[object] = []

    if account_ids:
        placeholders = ",".join(["?"] * len(account_ids))
        conditions.append(f"id IN ({placeholders})")
        params.extend(account_ids)

    if has_enable:
        conditions.append("LOWER(COALESCE(enable_rule_aware, 'false')) = 'true'")
    elif has_agent_type:
        conditions.append("LOWER(COALESCE(agent_type, '')) = 'rule_aware'")
    else:
        # Fail closed: this report is intended for rule-aware accounts only.
        # If the schema has no reliable marker, do not silently include all accounts.
        conditions.append("1 = 0")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id"

    rows = conn.execute(query, params).fetchall()
    return [
        AccountInfo(
            id=int(r["id"]),
            name=str(r["name"]),
            enable_rule_aware=r["enable_rule_aware"],
            agent_type=r["agent_type"],
            initial_capital=(float(r["initial_capital"]) if r["initial_capital"] is not None else None),
        )
        for r in rows
    ]


def calc_performance_stats(
    conn: sqlite3.Connection,
    account_id: int,
    initial_capital: Optional[float],
) -> Optional[Dict[str, object]]:
    """Calculate performance stats from persisted 1h asset curve snapshots.

    Final equity / return are sourced from `asset_curve_snapshots.total_assets`
    under `timeframe='1h'`, using the latest point per account. This keeps the
    final performance summary aligned with the benchmark's hourly checkpoint
    curve rather than per-account asynchronous account snapshots.
    """

    if not table_exists(conn, "asset_curve_snapshots"):
        return None

    rows = conn.execute(
        """
        SELECT timestamp, datetime_str, total_assets, profit, profit_percentage, cash, positions_value
        FROM asset_curve_snapshots
        WHERE account_id = ?
          AND timeframe = '1h'
        ORDER BY timestamp ASC
        """,
        (account_id,),
    ).fetchall()
    if not rows:
        return None

    def _to_float(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _round_money(value: Optional[float]) -> Optional[float]:
        return round(value, 2) if value is not None else None

    first = rows[0]
    latest = rows[-1]
    first_equity = _to_float(first["total_assets"])
    final_equity = _to_float(latest["total_assets"])

    total_pnl = None
    final_return_pct = None
    if initial_capital is not None and initial_capital > 0 and final_equity is not None:
        total_pnl = final_equity - initial_capital
        final_return_pct = (total_pnl / initial_capital) * 100.0
    elif latest["profit"] is not None:
        total_pnl = _to_float(latest["profit"])
        final_return_pct = _to_float(latest["profit_percentage"])

    curve_return_pct = None
    if first_equity is not None and first_equity > 0 and final_equity is not None:
        curve_return_pct = ((final_equity - first_equity) / first_equity) * 100.0

    max_drawdown_pct = None
    peak_equity = 0.0
    max_drawdown = 0.0
    for row in rows:
        equity = _to_float(row["total_assets"])
        if equity is None:
            continue
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
    if peak_equity > 0:
        max_drawdown_pct = max_drawdown * 100.0

    return {
        "curve_point_count": len(rows),
        "first_curve_ts": first["datetime_str"],
        "latest_equity_ts": latest["datetime_str"],
        "final_value_source": "asset_curve_snapshots:1h",
        "initial_capital": _round_money(initial_capital),
        "first_equity": _round_money(first_equity),
        "final_equity": _round_money(final_equity),
        "total_pnl": _round_money(total_pnl),
        "final_return_pct": round(final_return_pct, 3) if final_return_pct is not None else None,
        "curve_return_pct": round(curve_return_pct, 3) if curve_return_pct is not None else None,
        "max_drawdown_pct": round(max_drawdown_pct, 3) if max_drawdown_pct is not None else None,
        # Current 1h curve rows are checkpoint-backed and do not preserve a reliable
        # cash/positions decomposition, so leave these blank in the summary.
        "latest_cash": None,
        "latest_positions_value": None,
        "latest_snapshot_ts": None,
    }


def calc_snapshot_reference_stats(
    conn: sqlite3.Connection,
    account_id: int,
) -> Dict[str, object]:
    """Load latest raw account snapshot fields for reference only."""

    if not table_exists(conn, "account_snapshots"):
        return {}

    rows = conn.execute(
        """
        SELECT ts, cash, positions_value
        FROM account_snapshots
        WHERE account_id = ?
        ORDER BY ts ASC
        """,
        (account_id,),
    ).fetchall()
    if not rows:
        return {}

    def _to_float(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _round_money(value: Optional[float]) -> Optional[float]:
        return round(value, 2) if value is not None else None

    latest = rows[-1]
    latest_cash = _to_float(latest["cash"])
    latest_positions_value = _to_float(latest["positions_value"])

    return {
        "latest_snapshot_ts": latest["ts"],
        "latest_cash": _round_money(latest_cash),
        "latest_positions_value": _round_money(latest_positions_value),
    }


def calc_account_stats(conn: sqlite3.Connection, account_id: int) -> Dict[str, object]:
    rows = conn.execute(
        """
        SELECT ts, gate_pass, s_rule_sat, s_audit, final_score,
               llm_audit_score, llm_audit_coverage, llm_audit_conflict
        FROM rule_evaluation_results
        WHERE account_id = ?
        ORDER BY ts ASC
        """,
        (account_id,),
    ).fetchall()

    total_count = len(rows)
    if total_count == 0:
        return {
            "total_evaluations": 0,
            "all_time": None,
            "recent_7d": None,
            "llm_audit_stats": None,
            "latest": None,
        }

    def _avg(vals: Sequence[Optional[float]]) -> Optional[float]:
        non_null = [float(v) for v in vals if v is not None]
        if not non_null:
            return None
        return round(sum(non_null) / len(non_null), 3)

    gate_all = [1 if str(r["gate_pass"]).lower() == "true" else 0 for r in rows]
    all_time = {
        "gate_pass_rate": round(sum(gate_all) / total_count, 3),
        "avg_final_score": _avg([r["final_score"] for r in rows]),
        "avg_s_rule_sat": _avg([r["s_rule_sat"] for r in rows]),
        "avg_s_audit": _avg([r["s_audit"] for r in rows]),
        "evaluation_count": total_count,
    }

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent_rows = [r for r in rows if (parse_ts(r["ts"]) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]

    recent_7d = None
    if recent_rows:
        gate_recent = [1 if str(r["gate_pass"]).lower() == "true" else 0 for r in recent_rows]
        recent_7d = {
            "gate_pass_rate": round(sum(gate_recent) / len(recent_rows), 3),
            "avg_final_score": _avg([r["final_score"] for r in recent_rows]),
            "avg_s_rule_sat": _avg([r["s_rule_sat"] for r in recent_rows]),
            "avg_s_audit": _avg([r["s_audit"] for r in recent_rows]),
            "evaluation_count": len(recent_rows),
        }

    llm_rows = [r for r in rows if r["llm_audit_score"] is not None]
    llm_audit_stats = None
    if llm_rows:
        llm_audit_stats = {
            "count": len(llm_rows),
            "avg_score": _avg([r["llm_audit_score"] for r in llm_rows]),
            "avg_coverage": _avg([r["llm_audit_coverage"] for r in llm_rows]),
            "avg_conflict": _avg([r["llm_audit_conflict"] for r in llm_rows]),
        }

    latest = rows[-1]
    latest_data = {
        "timestamp": latest["ts"],
        "s_rule_sat": latest["s_rule_sat"],
        "s_audit": latest["s_audit"],
        "final_score": latest["final_score"],
    }

    return {
        "total_evaluations": total_count,
        "all_time": all_time,
        "recent_7d": recent_7d,
        "llm_audit_stats": llm_audit_stats,
        "latest": latest_data,
    }


def load_history_rows(
    conn: sqlite3.Connection,
    account_ids: Sequence[int],
    days: int,
) -> Dict[int, List[sqlite3.Row]]:
    if not account_ids:
        return {}
    placeholders = ",".join(["?"] * len(account_ids))
    rows = conn.execute(
        f"""
        SELECT account_id, ts, s_rule_sat, s_audit, final_score
        FROM rule_evaluation_results
        WHERE account_id IN ({placeholders})
        ORDER BY ts ASC
        """,
        list(account_ids),
    ).fetchall()

    anchor_ts = latest_row_datetime(rows, "ts")
    cutoff = (anchor_ts - timedelta(days=max(1, days))) if anchor_ts else None
    grouped: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        ts = parse_ts(row["ts"])
        if cutoff is not None and (ts is None or ts < cutoff):
            continue
        grouped[int(row["account_id"])].append(row)
    return grouped


def load_return_history_rows(
    conn: sqlite3.Connection,
    account_ids: Sequence[int],
    days: int,
) -> Dict[int, List[sqlite3.Row]]:
    if not account_ids or not table_exists(conn, "asset_curve_snapshots"):
        return {}
    placeholders = ",".join(["?"] * len(account_ids))
    rows = conn.execute(
        f"""
        SELECT account_id, timestamp, datetime_str, total_assets
        FROM asset_curve_snapshots
        WHERE account_id IN ({placeholders})
          AND timeframe = '1h'
        ORDER BY timestamp ASC
        """,
        list(account_ids),
    ).fetchall()

    anchor_ts = max((int(row["timestamp"]) for row in rows), default=None)
    cutoff_ts = (anchor_ts - max(1, days) * 24 * 60 * 60) if anchor_ts is not None else None
    grouped: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        row_ts = int(row["timestamp"])
        if cutoff_ts is not None and row_ts < cutoff_ts:
            continue
        grouped[int(row["account_id"])].append(row)
    return grouped


def load_snapshot_history_rows(
    conn: sqlite3.Connection,
    account_ids: Sequence[int],
    days: int,
) -> Dict[int, List[sqlite3.Row]]:
    if not account_ids or not table_exists(conn, "account_snapshots"):
        return {}
    placeholders = ",".join(["?"] * len(account_ids))
    rows = conn.execute(
        f"""
        SELECT account_id, ts, total_equity
        FROM account_snapshots
        WHERE account_id IN ({placeholders})
        ORDER BY ts ASC
        """,
        list(account_ids),
    ).fetchall()

    anchor_ts = latest_row_datetime(rows, "ts")
    cutoff = (anchor_ts - timedelta(days=max(1, days))) if anchor_ts else None
    grouped: Dict[int, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        ts = parse_ts(row["ts"])
        if cutoff is not None and (ts is None or ts < cutoff):
            continue
        grouped[int(row["account_id"])].append(row)
    return grouped


def build_trend_series(
    accounts: Sequence[AccountInfo],
    history_by_account: Dict[int, List[sqlite3.Row]],
    metric: str,
) -> Dict[int, Tuple[List[datetime], List[float]]]:
    bucket_map: Dict[int, Dict[datetime, List[float]]] = defaultdict(lambda: defaultdict(list))

    for account in accounts:
        for row in history_by_account.get(account.id, []):
            ts = parse_ts(row["ts"])
            if not ts:
                continue
            value = row[metric]
            if value is None:
                continue
            bucket = normalize_to_five_minutes(ts)
            bucket_map[account.id][bucket].append(float(value))

    series: Dict[int, Tuple[List[datetime], List[float]]] = {}
    for account in accounts:
        buckets = bucket_map.get(account.id, {})
        xs = sorted(buckets.keys())
        ys = [sum(buckets[ts]) / len(buckets[ts]) for ts in xs]
        series[account.id] = (xs, ys)
    return series


def build_return_series(
    accounts: Sequence[AccountInfo],
    history_by_account: Dict[int, List[sqlite3.Row]],
) -> Dict[int, Tuple[List[datetime], List[float]]]:
    """Build return percentage series from 1h asset_curve_snapshots.total_assets."""

    bucket_map: Dict[int, Dict[datetime, List[float]]] = defaultdict(lambda: defaultdict(list))
    initial_by_account = {
        account.id: account.initial_capital
        for account in accounts
        if account.initial_capital is not None and account.initial_capital > 0
    }

    for account in accounts:
        initial = initial_by_account.get(account.id)
        if not initial:
            continue
        for row in history_by_account.get(account.id, []):
            ts = parse_ts(row["datetime_str"])
            if not ts:
                continue
            try:
                equity = float(row["total_assets"])
            except (TypeError, ValueError):
                continue
            bucket = ts
            bucket_map[account.id][bucket].append(((equity - initial) / initial) * 100.0)

    series: Dict[int, Tuple[List[datetime], List[float]]] = {}
    for account in accounts:
        buckets = bucket_map.get(account.id, {})
        xs = sorted(buckets.keys())
        ys = [sum(buckets[ts]) / len(buckets[ts]) for ts in xs]
        series[account.id] = (xs, ys)
    return series


def plot_metric(
    output_path: Path,
    title: str,
    series: Dict[int, Tuple[List[datetime], List[float]]],
    accounts: Sequence[AccountInfo],
    *,
    y_label: str = "Score",
    y_limits: Optional[Tuple[float, float]] = (0.0, 1.0),
) -> None:
    if plt is None or mdates is None:
        raise RuntimeError("matplotlib is required for chart generation.")

    fig, ax = plt.subplots(figsize=(14, 6), dpi=140)
    for account in accounts:
        x_vals, y_vals = series.get(account.id, ([], []))
        if not x_vals:
            continue
        ax.plot(x_vals, y_vals, linewidth=1.8, marker="o", markersize=2.5, label=f"{account.name} (#{account.id})")

    ax.set_title(title)
    if y_limits is not None:
        ax.set_ylim(*y_limits)
    ax.set_ylabel(y_label)
    ax.set_xlabel("Time (UTC)")
    ax.grid(alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate(rotation=35)
    fig.tight_layout()
    fig.savefig(output_path)
    plt.close(fig)


def save_summary_csv(output_path: Path, rows: List[Dict[str, object]]) -> None:
    fieldnames = [
        "account_id",
        "account_name",
        "initial_capital",
        "final_equity",
        "total_pnl",
        "final_return_pct",
        "max_drawdown_pct",
        "final_value_source",
        "latest_cash",
        "latest_positions_value",
        "latest_equity_ts",
        "latest_snapshot_ts",
        "snapshot_latest_cash_ref",
        "snapshot_latest_positions_value_ref",
        "total_evaluations",
        "all_gate_pass_rate",
        "all_avg_s_rule_sat",
        "all_avg_s_audit",
        "all_avg_final_score",
        "recent7_gate_pass_rate",
        "recent7_avg_s_rule_sat",
        "recent7_avg_s_audit",
        "recent7_avg_final_score",
        "llm_audit_count",
        "llm_avg_score",
        "llm_avg_coverage",
        "llm_avg_conflict",
        "latest_ts",
        "latest_s_rule_sat",
        "latest_s_audit",
        "latest_final_score",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser().resolve()
    run_tag = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    output_dir = output_root / run_tag
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_db(db_path)
    try:
        accounts = pick_rule_aware_accounts(conn, args.account_id)
        if not accounts:
            print("No rule-aware accounts found under the current filter.")
            print(f"Database: {db_path}")
            return

        print(f"Database: {db_path}")
        print(f"Rule-aware accounts: {len(accounts)}")
        print("Performance source: asset_curve_snapshots(timeframe=1h, final point per account)")
        print("-" * 88)

        summary_rows: List[Dict[str, object]] = []
        stats_by_account: Dict[int, Dict[str, object]] = {}
        for account in accounts:
            stats = calc_account_stats(conn, account.id)
            stats_by_account[account.id] = stats
            perf = calc_performance_stats(conn, account.id, account.initial_capital) or {}
            snapshot_ref = calc_snapshot_reference_stats(conn, account.id)

            all_time = stats.get("all_time") or {}
            recent = stats.get("recent_7d") or {}
            llm = stats.get("llm_audit_stats") or {}
            latest = stats.get("latest") or {}

            print(
                f"#{account.id:<3} {account.name:<20} "
                f"evals={stats['total_evaluations']:<4} "
                f"pass={all_time.get('gate_pass_rate', 0):.3f} "
                f"score={all_time.get('avg_final_score')} "
                f"return={perf.get('final_return_pct')}% "
                f"equity={perf.get('final_equity')}"
            )

            summary_rows.append(
                {
                    "account_id": account.id,
                    "account_name": account.name,
                    "initial_capital": perf.get("initial_capital"),
                    "final_equity": perf.get("final_equity"),
                    "total_pnl": perf.get("total_pnl"),
                    "final_return_pct": perf.get("final_return_pct"),
                    "max_drawdown_pct": perf.get("max_drawdown_pct"),
                    "final_value_source": perf.get("final_value_source"),
                    "latest_cash": perf.get("latest_cash"),
                    "latest_positions_value": perf.get("latest_positions_value"),
                    "latest_equity_ts": perf.get("latest_equity_ts"),
                    "latest_snapshot_ts": snapshot_ref.get("latest_snapshot_ts"),
                    "snapshot_latest_cash_ref": snapshot_ref.get("latest_cash"),
                    "snapshot_latest_positions_value_ref": snapshot_ref.get("latest_positions_value"),
                    "total_evaluations": stats["total_evaluations"],
                    "all_gate_pass_rate": all_time.get("gate_pass_rate"),
                    "all_avg_s_rule_sat": all_time.get("avg_s_rule_sat"),
                    "all_avg_s_audit": all_time.get("avg_s_audit"),
                    "all_avg_final_score": all_time.get("avg_final_score"),
                    "recent7_gate_pass_rate": recent.get("gate_pass_rate"),
                    "recent7_avg_s_rule_sat": recent.get("avg_s_rule_sat"),
                    "recent7_avg_s_audit": recent.get("avg_s_audit"),
                    "recent7_avg_final_score": recent.get("avg_final_score"),
                    "llm_audit_count": llm.get("count"),
                    "llm_avg_score": llm.get("avg_score"),
                    "llm_avg_coverage": llm.get("avg_coverage"),
                    "llm_avg_conflict": llm.get("avg_conflict"),
                    "latest_ts": latest.get("timestamp"),
                    "latest_s_rule_sat": latest.get("s_rule_sat"),
                    "latest_s_audit": latest.get("s_audit"),
                    "latest_final_score": latest.get("final_score"),
                }
            )

        summary_csv = output_dir / "account_summary.csv"
        save_summary_csv(summary_csv, summary_rows)

        summary_json = output_dir / "account_summary.json"
        summary_json.write_text(
            json.dumps(summary_rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        history = load_history_rows(conn, [a.id for a in accounts], args.days)
        if not args.no_charts:
            if plt is None:
                raise RuntimeError(
                    "matplotlib is not available. Install it or run with --no-charts."
                )

            for metric, title, filename in [
                ("s_rule_sat", "Rule Satisfaction Score Over Time", "trend_s_rule_sat.png"),
                ("s_audit", "LLM Audit Score Over Time", "trend_s_audit.png"),
                ("final_score", "Final Combined Score Over Time", "trend_final_score.png"),
            ]:
                series = build_trend_series(accounts, history, metric)
                if not any(series.get(a.id, ([], []))[0] for a in accounts):
                    continue
                plot_metric(output_dir / filename, title, series, accounts)

            return_history = load_return_history_rows(conn, [a.id for a in accounts], args.days)
            return_series = build_return_series(accounts, return_history)
            if any(return_series.get(a.id, ([], []))[0] for a in accounts):
                plot_metric(
                    output_dir / "trend_final_return_pct.png",
                    "Hourly Asset-Curve Return Percentage Over Time",
                    return_series,
                    accounts,
                    y_label="Return (%)",
                    y_limits=None,
                )

        print("-" * 88)
        print(f"Summary CSV:  {summary_csv}")
        print(f"Summary JSON: {summary_json}")
        if not args.no_charts:
            print(f"Charts dir:   {output_dir}")
        print("Done.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
