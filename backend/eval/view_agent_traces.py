"""
View agent_traces records by account_id from SQLite.

Usage (run from backend/):
  python eval/view_agent_traces.py --db-path ./alpha_arena.sqlite --account-id 12
  python eval/view_agent_traces.py --db-path ./alpha_arena.sqlite --account-id 12 --limit 200 --output-csv ./eval_outputs/agent_traces_acc12.csv
  python eval/view_agent_traces.py --db-path ./alpha_arena.sqlite --account-id 12 --limit 50 --skip-tool-io-content --full-content
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from typing import List, Sequence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="view_agent_traces",
        description="Query agent_traces by account_id from a SQLite database.",
    )
    parser.add_argument(
        "--db-path",
        default="./alpha_arena.sqlite",
        help="Path to SQLite database file (default: ./alpha_arena.sqlite)",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        default=12,
        help="Target account_id (default: 12)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max rows to print (default: 100)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Row offset for pagination (default: 0)",
    )
    parser.add_argument(
        "--trace-id",
        default=None,
        help="Optional trace_id filter",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional CSV output path",
    )
    parser.add_argument(
        "--content-max-len",
        type=int,
        default=180,
        help="Max content length shown in console (default: 180)",
    )
    parser.add_argument(
        "--full-content",
        action="store_true",
        help="Show full content/tool_calls/tool_output without truncation.",
    )
    parser.add_argument(
        "--skip-tool-io-content",
        action="store_true",
        help=(
            "Skip rows whose content text contains 'tool_calls' or 'tool_output' "
            "(also matches typo 'toll_output')."
        ),
    )
    return parser.parse_args()


def get_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(r[1]) for r in rows]


def truncate_text(text: object, max_len: int) -> str:
    if text is None:
        return ""
    value = str(text).replace("\n", " ").strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def should_skip_tool_io_row(row: sqlite3.Row, columns: Sequence[str]) -> bool:
    # Skip explicit tool role rows.
    if "role" in columns and str(row["role"] or "").lower() == "tool":
        return True

    # Skip rows that carry tool call/output payload columns.
    if "tool_calls" in columns and row["tool_calls"]:
        return True
    if "tool_output" in columns and row["tool_output"]:
        return True

    # Also match keyword text in content (including typo toll_output).
    if "content" in columns and row["content"] is not None:
        text = str(row["content"]).lower()
        if "tool_calls" in text or "tool_output" in text or "toll_output" in text:
            return True

    return False


def write_csv(output_path: Path, rows: Sequence[sqlite3.Row], columns: Sequence[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[col] for col in columns])


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        columns = get_columns(conn, "agent_traces")
        if not columns:
            raise RuntimeError("Table 'agent_traces' does not exist in this database.")

        where_clauses = ["account_id = ?"]
        params: List[object] = [args.account_id]

        if args.trace_id:
            where_clauses.append("trace_id = ?")
            params.append(args.trace_id)

        where_sql = " AND ".join(where_clauses)

        total_count = conn.execute(
            f"SELECT COUNT(*) FROM agent_traces WHERE {where_sql}",
            params,
        ).fetchone()[0]

        query = f"""
            SELECT *
            FROM agent_traces
            WHERE {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(query, params + [args.limit, args.offset]).fetchall()

        print(f"Database: {db_path}")
        print(f"account_id: {args.account_id}")
        if args.trace_id:
            print(f"trace_id: {args.trace_id}")
        print(f"total matched rows: {total_count}")
        print(f"showing: {len(rows)} rows (limit={args.limit}, offset={args.offset})")
        print("-" * 120)

        display_cols = [
            col
            for col in ["id", "trace_id", "account_id", "step_number", "role", "created_at"]
            if col in columns
        ]

        displayed = 0
        for row in rows:
            if args.skip_tool_io_content and should_skip_tool_io_row(row, columns):
                continue

            summary = " | ".join(f"{c}={row[c]}" for c in display_cols)
            print(summary)
            if "content" in columns:
                if args.full_content:
                    print("  content:", "" if row["content"] is None else str(row["content"]))
                else:
                    print("  content:", truncate_text(row["content"], args.content_max_len))
            if "tool_calls" in columns and row["tool_calls"]:
                if args.full_content:
                    print("  tool_calls:", str(row["tool_calls"]))
                else:
                    print("  tool_calls:", truncate_text(row["tool_calls"], args.content_max_len))
            if "tool_output" in columns and row["tool_output"]:
                if args.full_content:
                    print("  tool_output:", str(row["tool_output"]))
                else:
                    print("  tool_output:", truncate_text(row["tool_output"], args.content_max_len))
            print("-" * 120)
            displayed += 1

        if displayed == 0:
            print("No rows displayed after applying current filters.")
            print("Tip: increase --limit/--offset, or disable --skip-tool-io-content.")
            print("-" * 120)

        if args.output_csv:
            output_path = Path(args.output_csv).expanduser().resolve()
            write_csv(output_path, rows, columns)
            print(f"CSV saved: {output_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
