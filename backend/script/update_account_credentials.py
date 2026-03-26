#!/usr/bin/env python3
"""
Batch update accounts.api_key / accounts.base_url in SQLite DB.

Usage examples:

1) Update ALL accounts to same credentials:
   python script/update_account_credentials.py --all --api-key "sk-xxx" --base-url "https://api.openai.com/v1"

2) Update selected accounts by id:
   python script/update_account_credentials.py --ids 1,2,3 --api-key "sk-xxx"

3) Update selected accounts by name:
   python script/update_account_credentials.py --names GPT,Qwen --base-url "https://example.com/v1"

4) Dry-run (preview only):
   python script/update_account_credentials.py --all --api-key "sk-xxx" --dry-run

5) Mapping mode (different values per account):
   python script/update_account_credentials.py --mapping-json mappings.json

   mappings.json example:
   [
     {"id": 1, "api_key": "sk-a", "base_url": "https://a.example/v1"},
     {"name": "Qwen", "api_key": "sk-b"}
   ]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch replace account api_key/base_url in SQLite database."
    )
    parser.add_argument(
        "--db",
        default="./data.db",
        help="Path to SQLite DB file (default: ./data.db)",
    )
    parser.add_argument("--all", action="store_true", help="Target all accounts")
    parser.add_argument("--ids", help="Comma-separated account IDs, e.g. 1,2,3")
    parser.add_argument("--names", help="Comma-separated account names, e.g. GPT,Qwen")
    parser.add_argument("--api-key", help="New api_key value")
    parser.add_argument("--base-url", help="New base_url value")
    parser.add_argument(
        "--mapping-json",
        help="Path to JSON file for per-account updates",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes only")
    return parser.parse_args()


def _connect(db_path: str) -> sqlite3.Connection:
    p = Path(db_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"DB not found: {p}")
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def _split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _fetch_targets(conn: sqlite3.Connection, use_all: bool, ids: List[str], names: List[str]) -> List[sqlite3.Row]:
    if use_all:
        return conn.execute("SELECT id, name, base_url, api_key FROM accounts ORDER BY id").fetchall()

    clauses: List[str] = []
    params: List[object] = []
    if ids:
        placeholders = ",".join(["?"] * len(ids))
        clauses.append(f"id IN ({placeholders})")
        params.extend(int(x) for x in ids)
    if names:
        placeholders = ",".join(["?"] * len(names))
        clauses.append(f"name IN ({placeholders})")
        params.extend(names)

    if not clauses:
        return []

    sql = "SELECT id, name, base_url, api_key FROM accounts WHERE " + " OR ".join(clauses) + " ORDER BY id"
    return conn.execute(sql, params).fetchall()


def _mask(v: Optional[str]) -> str:
    if not v:
        return "(empty)"
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:]


def _print_preview(rows: List[sqlite3.Row], new_api_key: Optional[str], new_base_url: Optional[str]) -> None:
    print(f"Target accounts: {len(rows)}")
    for r in rows:
        next_key = r["api_key"] if new_api_key is None else new_api_key
        next_url = r["base_url"] if new_base_url is None else new_base_url
        print(
            f"- id={r['id']} name={r['name']} | "
            f"api_key: {_mask(r['api_key'])} -> {_mask(next_key)} | "
            f"base_url: {r['base_url'] or '(empty)'} -> {next_url or '(empty)'}"
        )


def _update_uniform(
    conn: sqlite3.Connection,
    rows: List[sqlite3.Row],
    new_api_key: Optional[str],
    new_base_url: Optional[str],
    dry_run: bool,
) -> int:
    from services.security.api_key_security import encrypt_api_key

    _print_preview(rows, new_api_key, new_base_url)
    if dry_run:
        print("[DRY-RUN] No rows updated.")
        return 0

    updated = 0
    for r in rows:
        api_key = r["api_key"] if new_api_key is None else encrypt_api_key(new_api_key)
        base_url = r["base_url"] if new_base_url is None else new_base_url
        conn.execute(
            """
            UPDATE accounts
               SET api_key = ?, base_url = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (api_key, base_url, r["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def _load_mapping(path: str) -> List[Dict]:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"mapping json not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("mapping json must be a list")
    return data


def _resolve_account_id(conn: sqlite3.Connection, item: Dict) -> int:
    if "id" in item and item["id"] is not None:
        return int(item["id"])
    if "name" in item and item["name"]:
        row = conn.execute("SELECT id FROM accounts WHERE name = ?", (str(item["name"]),)).fetchone()
        if not row:
            raise ValueError(f"Account name not found: {item['name']}")
        return int(row["id"])
    raise ValueError("Each mapping item requires either 'id' or 'name'")


def _update_mapping(conn: sqlite3.Connection, mappings: List[Dict], dry_run: bool) -> int:
    from services.security.api_key_security import encrypt_api_key

    updates: List[Tuple[int, Optional[str], Optional[str]]] = []
    for item in mappings:
        account_id = _resolve_account_id(conn, item)
        cur = conn.execute(
            "SELECT id, name, api_key, base_url FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not cur:
            raise ValueError(f"Account id not found: {account_id}")

        next_key = encrypt_api_key(item.get("api_key")) if "api_key" in item else cur["api_key"]
        next_url = item.get("base_url", cur["base_url"])
        print(
            f"- id={cur['id']} name={cur['name']} | "
            f"api_key: {_mask(cur['api_key'])} -> {_mask(next_key)} | "
            f"base_url: {cur['base_url'] or '(empty)'} -> {next_url or '(empty)'}"
        )
        updates.append((account_id, next_key, next_url))

    if dry_run:
        print("[DRY-RUN] No rows updated.")
        return 0

    for account_id, next_key, next_url in updates:
        conn.execute(
            """
            UPDATE accounts
               SET api_key = ?, base_url = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (next_key, next_url, account_id),
        )
    conn.commit()
    return len(updates)


def main() -> int:
    args = parse_args()

    has_uniform = (args.api_key is not None) or (args.base_url is not None)
    has_mapping = args.mapping_json is not None
    if has_uniform and has_mapping:
        raise SystemExit("Use either uniform mode (--api-key/--base-url) or --mapping-json, not both.")
    if not has_uniform and not has_mapping:
        raise SystemExit("Nothing to update. Provide --api-key/--base-url or --mapping-json.")

    with _connect(args.db) as conn:
        if has_mapping:
            mappings = _load_mapping(args.mapping_json)
            print(f"Loaded {len(mappings)} mapping items from {args.mapping_json}")
            updated = _update_mapping(conn, mappings, args.dry_run)
            print(f"Done. Updated rows: {updated}")
            return 0

        ids = _split_csv(args.ids)
        names = _split_csv(args.names)
        if not args.all and not ids and not names:
            raise SystemExit("Uniform mode requires one target selector: --all or --ids or --names.")

        rows = _fetch_targets(conn, args.all, ids, names)
        if not rows:
            raise SystemExit("No matching accounts found.")

        updated = _update_uniform(conn, rows, args.api_key, args.base_url, args.dry_run)
        print(f"Done. Updated rows: {updated}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
