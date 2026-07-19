"""
Delete a specific account from SQLite database.

Features:
- Delete by account ID
- Automatically remove related rows in tables that contain `account_id`
- Transaction-safe (commit on success, rollback on failure)

Usage:
  python delete_account.py --account-id 2
  python delete_account.py --account-id 2 --yes
  python delete_account.py --account-id 2 --db ./data.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Dict, List, Tuple


def get_db_path(cli_db_path: str | None) -> str:
    if cli_db_path:
        return os.path.abspath(cli_db_path)
    return os.path.join(os.path.dirname(__file__), "data.db")


def table_exists(cursor: sqlite3.Cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    )
    return cursor.fetchone() is not None


def get_all_user_tables(cursor: sqlite3.Cursor) -> List[str]:
    cursor.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [row[0] for row in cursor.fetchall()]


def get_account_related_tables(cursor: sqlite3.Cursor) -> List[str]:
    """Return all tables (except accounts) that have an `account_id` column."""
    related: List[str] = []
    for table in get_all_user_tables(cursor):
        if table == "accounts":
            continue
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        if any(col[1] == "account_id" for col in columns):
            related.append(table)
    return related


def account_exists(cursor: sqlite3.Cursor, account_id: int) -> Tuple[bool, str | None]:
    cursor.execute("SELECT name FROM accounts WHERE id = ?", (account_id,))
    row = cursor.fetchone()
    if row:
        return True, row[0]
    return False, None


def preview_related_counts(cursor: sqlite3.Cursor, account_id: int) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table in get_account_related_tables(cursor):
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id = ?", (account_id,))
        counts[table] = int(cursor.fetchone()[0])
    return counts


def delete_account_and_related(conn: sqlite3.Connection, account_id: int) -> Dict[str, int]:
    cursor = conn.cursor()

    if not table_exists(cursor, "accounts"):
        raise RuntimeError("Table `accounts` does not exist.")

    exists, _ = account_exists(cursor, account_id)
    if not exists:
        raise RuntimeError(f"Account id={account_id} not found.")

    deleted: Dict[str, int] = {}

    # Delete child rows first to avoid foreign key constraint failures.
    for table in get_account_related_tables(cursor):
        cursor.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
        deleted[table] = cursor.rowcount

    cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    deleted["accounts"] = cursor.rowcount

    return deleted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete a specific account and its related data.")
    parser.add_argument("--account-id", type=int, required=True, help="Account ID to delete")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite database (default: backend/data.db)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = get_db_path(args.db)

    if not os.path.exists(db_path):
        print(f"ERROR: Database file not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()

        exists, account_name = account_exists(cursor, args.account_id)
        if not exists:
            print(f"ERROR: Account id={args.account_id} does not exist.")
            return 1

        counts = preview_related_counts(cursor, args.account_id)

        print("=" * 64)
        print("Delete Account Preview")
        print("=" * 64)
        print(f"DB: {db_path}")
        print(f"Account: id={args.account_id}, name={account_name}")
        print("Related rows to delete:")
        total_related = 0
        for table in sorted(counts.keys()):
            cnt = counts[table]
            total_related += cnt
            print(f"  - {table}: {cnt}")
        print(f"  - accounts: 1")
        print(f"Total rows (including accounts): {total_related + 1}")
        print("=" * 64)

        if not args.yes:
            confirm = input("Type 'yes' to continue deletion: ").strip().lower()
            if confirm != "yes":
                print("Cancelled.")
                return 0

        conn.execute("BEGIN")
        deleted = delete_account_and_related(conn, args.account_id)
        conn.commit()

        print("Deletion completed successfully.")
        for table in sorted(deleted.keys()):
            print(f"  - {table}: deleted {deleted[table]}")
        return 0

    except Exception as err:
        conn.rollback()
        print(f"ERROR: {err}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
