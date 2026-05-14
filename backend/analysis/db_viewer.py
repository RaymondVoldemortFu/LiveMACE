from __future__ import annotations

import argparse
import ast
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "alpha_arena_final.sqlite"

app = FastAPI(title="Alpha Arena DB Viewer", docs_url="/docs", redoc_url=None)
_DB_PATH = DEFAULT_DB_PATH


def _configure_db_path(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"SQLite database not found: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"SQLite path is not a file: {resolved}")
    global _DB_PATH
    _DB_PATH = resolved


def _connect() -> sqlite3.Connection:
    uri = f"file:{_DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _parse_maybe_json(raw: Any) -> Any:
    if raw is None or not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return raw


def _get_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _ensure_table(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if table not in _get_tables(conn):
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")
    columns = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    if not columns:
        raise HTTPException(status_code=404, detail=f"Table has no schema: {table}")
    return columns


def _bounded_limit(limit: int, max_limit: int = 500) -> int:
    return max(1, min(limit, max_limit))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return HTML_PAGE


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "db_path": str(_DB_PATH)}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    with _connect() as conn:
        tables = []
        for table in _get_tables(conn):
            columns = _ensure_table(conn, table)
            count = conn.execute(f"SELECT COUNT(*) AS c FROM {_quote_identifier(table)}").fetchone()["c"]
            tables.append(
                {
                    "name": table,
                    "row_count": count,
                    "columns": [
                        {
                            "name": row["name"],
                            "type": row["type"],
                            "notnull": bool(row["notnull"]),
                            "pk": bool(row["pk"]),
                        }
                        for row in columns
                    ],
                }
            )
    return {"db_path": str(_DB_PATH), "tables": tables}


@app.get("/api/accounts")
def accounts() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.name,
                a.account_type,
                a.agent_type,
                a.model,
                a.is_active,
                a.current_cash,
                a.margin_used,
                a.created_at,
                COALESCE(t.trace_count, 0) AS trace_count,
                COALESCE(t.step_count, 0) AS step_count,
                COALESCE(d.decision_count, 0) AS decision_count,
                COALESCE(d.decision_trace_count, 0) AS decision_trace_count,
                t.first_trace_at,
                t.last_trace_at,
                d.first_decision_at,
                d.last_decision_at
            FROM accounts a
            LEFT JOIN (
                SELECT
                    account_id,
                    COUNT(DISTINCT trace_id) AS trace_count,
                    COUNT(*) AS step_count,
                    MIN(created_at) AS first_trace_at,
                    MAX(created_at) AS last_trace_at
                FROM agent_traces
                GROUP BY account_id
            ) t ON t.account_id = a.id
            LEFT JOIN (
                SELECT
                    account_id,
                    COUNT(*) AS decision_count,
                    COUNT(DISTINCT trace_id) AS decision_trace_count,
                    MIN(decision_time) AS first_decision_at,
                    MAX(decision_time) AS last_decision_at
                FROM ai_decision_logs
                GROUP BY account_id
            ) d ON d.account_id = a.id
            ORDER BY a.id
            """
        ).fetchall()
    return _rows_to_dicts(rows)


@app.get("/api/traces")
def traces(
    account_id: int | None = None,
    q: str = "",
    operation: str = "",
    symbol: str = "",
    start_at: str = "",
    end_at: str = "",
    sort_by: str = "started_at",
    sort_dir: str = "desc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    limit = _bounded_limit(limit)
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    sort_map = {
        "account_id": "m.account_id",
        "started_at": "m.started_at",
        "ended_at": "m.ended_at",
        "step_count": "m.step_count",
        "decision_count": "m.decision_count",
        "trace_id": "m.trace_id",
    }
    order_expr = sort_map.get(sort_by, "m.started_at")

    where = ["1 = 1"]
    params: list[Any] = []
    if account_id is not None:
        where.append("m.account_id = ?")
        params.append(account_id)
    if q.strip():
        needle = f"%{q.strip()}%"
        where.append(
            """
            (
                m.trace_id LIKE ?
                OR m.account_name LIKE ?
                OR COALESCE(m.latest_reason, '') LIKE ?
                OR COALESCE(m.operations, '') LIKE ?
                OR COALESCE(m.symbols, '') LIKE ?
            )
            """
        )
        params.extend([needle, needle, needle, needle, needle])
    if operation.strip():
        where.append("COALESCE(m.operations, '') LIKE ?")
        params.append(f"%{operation.strip()}%")
    if symbol.strip():
        where.append("COALESCE(m.symbols, '') LIKE ?")
        params.append(f"%{symbol.strip()}%")
    if start_at.strip():
        where.append("m.started_at >= ?")
        params.append(start_at.strip())
    if end_at.strip():
        where.append("m.started_at <= ?")
        params.append(end_at.strip())

    sql = f"""
        WITH trace_keys AS (
            SELECT trace_id, account_id
            FROM agent_traces
            WHERE trace_id IS NOT NULL
            GROUP BY trace_id, account_id
            UNION
            SELECT trace_id, account_id
            FROM ai_decision_logs
            WHERE trace_id IS NOT NULL
            GROUP BY trace_id, account_id
        ),
        step_agg AS (
            SELECT
                trace_id,
                account_id,
                COUNT(*) AS step_count,
                MIN(created_at) AS first_step_at,
                MAX(created_at) AS last_step_at
            FROM agent_traces
            WHERE trace_id IS NOT NULL
            GROUP BY trace_id, account_id
        ),
        decision_agg AS (
            SELECT
                trace_id,
                account_id,
                COUNT(*) AS decision_count,
                MIN(decision_time) AS first_decision_at,
                MAX(decision_time) AS last_decision_at,
                GROUP_CONCAT(DISTINCT operation) AS operations,
                GROUP_CONCAT(DISTINCT symbol) AS symbols
            FROM ai_decision_logs
            WHERE trace_id IS NOT NULL
            GROUP BY trace_id, account_id
        ),
        latest_decision AS (
            SELECT *
            FROM (
                SELECT
                    d.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY d.trace_id, d.account_id
                        ORDER BY d.decision_time DESC, d.id DESC
                    ) AS rn
                FROM ai_decision_logs d
                WHERE d.trace_id IS NOT NULL
            )
            WHERE rn = 1
        ),
        merged AS (
            SELECT
                k.trace_id,
                k.account_id,
                a.name AS account_name,
                COALESCE(da.first_decision_at, sa.first_step_at) AS started_at,
                COALESCE(sa.last_step_at, da.last_decision_at) AS ended_at,
                COALESCE(sa.step_count, 0) AS step_count,
                COALESCE(da.decision_count, 0) AS decision_count,
                da.operations,
                da.symbols,
                ld.operation AS latest_operation,
                ld.symbol AS latest_symbol,
                ld.direction AS latest_direction,
                ld.executed AS latest_executed,
                ld.reason AS latest_reason,
                ld.total_balance AS latest_total_balance
            FROM trace_keys k
            JOIN accounts a ON a.id = k.account_id
            LEFT JOIN step_agg sa ON sa.trace_id = k.trace_id AND sa.account_id = k.account_id
            LEFT JOIN decision_agg da ON da.trace_id = k.trace_id AND da.account_id = k.account_id
            LEFT JOIN latest_decision ld ON ld.trace_id = k.trace_id AND ld.account_id = k.account_id
        )
        SELECT m.*, COUNT(*) OVER() AS total
        FROM merged m
        WHERE {" AND ".join(where)}
        ORDER BY {order_expr} {direction}, m.trace_id {direction}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    total = int(rows[0]["total"]) if rows else 0
    items = []
    for row in rows:
        data = dict(row)
        data.pop("total", None)
        reason = data.get("latest_reason")
        data["reason_preview"] = (reason[:240] + "...") if isinstance(reason, str) and len(reason) > 240 else reason
        items.append(data)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/trace/{trace_id}")
def trace_detail(trace_id: str, account_id: int | None = None) -> dict[str, Any]:
    params: list[Any] = [trace_id]
    account_clause = ""
    if account_id is not None:
        account_clause = " AND account_id = ?"
        params.append(account_id)

    with _connect() as conn:
        steps = conn.execute(
            f"""
            SELECT step_number, role, content, tool_calls, tool_output, created_at, account_id
            FROM agent_traces
            WHERE trace_id = ?{account_clause}
            ORDER BY step_number, id
            """,
            params,
        ).fetchall()
        decisions = conn.execute(
            f"""
            SELECT *
            FROM ai_decision_logs
            WHERE trace_id = ?{account_clause}
            ORDER BY decision_time, id
            """,
            params,
        ).fetchall()
        account_row = None
        effective_account_id = account_id
        if effective_account_id is None:
            if steps:
                effective_account_id = steps[0]["account_id"]
            elif decisions:
                effective_account_id = decisions[0]["account_id"]
        if effective_account_id is not None:
            account_row = conn.execute(
                """
                SELECT id, name, account_type, agent_type, model, is_active
                FROM accounts
                WHERE id = ?
                """,
                [effective_account_id],
            ).fetchone()

    if not steps and not decisions:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")

    normalized_steps = []
    for step in steps:
        data = dict(step)
        data["tool_calls"] = _parse_maybe_json(data.get("tool_calls"))
        data["tool_output"] = _parse_maybe_json(data.get("tool_output"))
        normalized_steps.append(data)

    return {
        "trace_id": trace_id,
        "account": dict(account_row) if account_row else None,
        "steps": normalized_steps,
        "decisions": _rows_to_dicts(decisions),
    }


@app.get("/api/db/{table}/rows")
def table_rows(
    table: str,
    q: str = "",
    filters: str = "",
    sort_by: str = "",
    sort_dir: str = "desc",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    limit = _bounded_limit(limit)
    direction = "ASC" if sort_dir.lower() == "asc" else "DESC"
    with _connect() as conn:
        columns = _ensure_table(conn, table)
        column_names = [str(row["name"]) for row in columns]
        column_set = set(column_names)

        where = ["1 = 1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            where.append(
                "("
                + " OR ".join(f"CAST({_quote_identifier(col)} AS TEXT) LIKE ?" for col in column_names)
                + ")"
            )
            params.extend([needle] * len(column_names))

        if filters.strip():
            try:
                filter_items = json.loads(filters)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid filters JSON: {exc}") from exc
            if not isinstance(filter_items, list):
                raise HTTPException(status_code=400, detail="Filters must be a JSON list")
            for item in filter_items:
                if not isinstance(item, dict):
                    raise HTTPException(status_code=400, detail="Each filter must be an object")
                col = str(item.get("column", ""))
                op = str(item.get("op", "contains"))
                value = item.get("value", "")
                if col not in column_set:
                    raise HTTPException(status_code=400, detail=f"Unknown column in filter: {col}")
                quoted = _quote_identifier(col)
                if op == "contains":
                    where.append(f"CAST({quoted} AS TEXT) LIKE ?")
                    params.append(f"%{value}%")
                elif op == "eq":
                    where.append(f"{quoted} = ?")
                    params.append(value)
                elif op == "ne":
                    where.append(f"{quoted} != ?")
                    params.append(value)
                elif op == "gt":
                    where.append(f"{quoted} > ?")
                    params.append(value)
                elif op == "gte":
                    where.append(f"{quoted} >= ?")
                    params.append(value)
                elif op == "lt":
                    where.append(f"{quoted} < ?")
                    params.append(value)
                elif op == "lte":
                    where.append(f"{quoted} <= ?")
                    params.append(value)
                elif op == "is_null":
                    where.append(f"{quoted} IS NULL")
                elif op == "not_null":
                    where.append(f"{quoted} IS NOT NULL")
                else:
                    raise HTTPException(status_code=400, detail=f"Unsupported filter op: {op}")

        order_col = sort_by if sort_by in column_set else column_names[0]
        table_name = _quote_identifier(table)
        order_expr = _quote_identifier(order_col)
        sql = f"""
            SELECT *, COUNT(*) OVER() AS __total
            FROM {table_name}
            WHERE {" AND ".join(where)}
            ORDER BY {order_expr} {direction}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()

    total = int(rows[0]["__total"]) if rows else 0
    items = []
    for row in rows:
        data = dict(row)
        data.pop("__total", None)
        items.append(data)
    return {
        "table": table,
        "columns": [{"name": row["name"], "type": row["type"]} for row in columns],
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort_by": order_col,
        "sort_dir": direction.lower(),
    }


HTML_PAGE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Alpha Arena DB Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7fb;
      --panel: #ffffff;
      --panel-2: #f9fafb;
      --border: #e5e7eb;
      --text: #111827;
      --muted: #6b7280;
      --primary: #2563eb;
      --primary-soft: #dbeafe;
      --green: #059669;
      --purple: #7c3aed;
      --amber: #d97706;
      --red: #dc2626;
      --shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    button, input, select { font: inherit; }
    button {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 8px 12px;
      background: #fff;
      color: var(--text);
      cursor: pointer;
    }
    button.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
    button:disabled { cursor: not-allowed; opacity: .5; }
    input, select {
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 8px 10px;
      min-width: 0;
      background: #fff;
      color: var(--text);
    }
    .app { height: 100vh; display: grid; grid-template-rows: auto 1fr; }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      background: var(--panel);
      border-bottom: 1px solid var(--border);
      box-shadow: 0 2px 14px rgba(15, 23, 42, 0.04);
    }
    .title { display: flex; flex-direction: column; gap: 2px; }
    .title h1 { margin: 0; font-size: 18px; }
    .title span { color: var(--muted); font-size: 12px; }
    .tabs { display: flex; gap: 8px; }
    .tab { border-radius: 999px; }
    .tab.active { background: var(--primary); border-color: var(--primary); color: #fff; }
    main { min-height: 0; }
    .view { height: 100%; display: none; }
    .view.active { display: block; }
    .trace-layout {
      height: 100%;
      display: grid;
      grid-template-columns: 320px 390px minmax(0, 1fr);
      gap: 12px;
      padding: 12px;
      min-height: 0;
    }
    .panel {
      min-height: 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: var(--shadow);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .panel-header {
      padding: 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .panel-header h2 { margin: 0; font-size: 15px; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .toolbar input, .toolbar select { flex: 1; min-width: 120px; }
    .list { overflow: auto; padding: 8px; min-height: 0; }
    .item {
      padding: 10px;
      border: 1px solid transparent;
      border-radius: 12px;
      cursor: pointer;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .item:hover { background: var(--panel-2); }
    .item.active { border-color: var(--primary); background: var(--primary-soft); }
    .item-title { font-weight: 650; font-size: 13px; display: flex; justify-content: space-between; gap: 8px; }
    .item-sub { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .badges { display: flex; gap: 6px; flex-wrap: wrap; }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 11px;
      background: #eef2ff;
      color: #3730a3;
    }
    .badge.green { background: #d1fae5; color: #065f46; }
    .badge.gray { background: #f3f4f6; color: #374151; }
    .badge.purple { background: #ede9fe; color: #5b21b6; }
    .badge.amber { background: #fef3c7; color: #92400e; }
    .pager {
      padding: 10px 12px;
      border-top: 1px solid var(--border);
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      color: var(--muted);
      font-size: 12px;
    }
    .chat-header {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
    }
    .chat-title { min-width: 0; }
    .chat-title strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .chat-title span { color: var(--muted); font-size: 12px; }
    .chat-scroll { overflow: auto; padding: 18px; min-height: 0; background: linear-gradient(#fff, #fafafa); }
    .empty {
      height: 100%;
      display: grid;
      place-items: center;
      text-align: center;
      color: var(--muted);
      padding: 24px;
    }
    .message { display: flex; margin-bottom: 16px; gap: 10px; align-items: flex-start; }
    .message.user { flex-direction: row-reverse; }
    .avatar {
      width: 32px;
      height: 32px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      background: var(--green);
      color: #fff;
      font-weight: 700;
      font-size: 12px;
    }
    .message.user .avatar { background: var(--primary); }
    .message.tool .avatar { background: var(--purple); }
    .message.memory .avatar { background: var(--amber); }
    .bubble-wrap { max-width: min(820px, 82%); min-width: 0; }
    .message.user .bubble-wrap { display: flex; flex-direction: column; align-items: flex-end; }
    .meta-line { color: var(--muted); font-size: 11px; margin-bottom: 5px; display: flex; gap: 6px; flex-wrap: wrap; }
    .bubble {
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 11px 12px;
      background: #fff;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 13px;
      line-height: 1.5;
    }
    .message.user .bubble { background: var(--primary); color: #fff; border-color: var(--primary); }
    .message.tool .bubble { background: #faf5ff; }
    .json-block, .think-block {
      margin-top: 10px;
      padding: 10px;
      border-radius: 10px;
      background: rgba(17, 24, 39, .045);
      border: 1px solid rgba(17, 24, 39, .08);
      overflow: auto;
    }
    .json-block strong, .think-block strong { display: block; margin-bottom: 5px; color: var(--muted); font-size: 11px; }
    pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
    details.decision {
      margin-bottom: 12px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: #fff;
      padding: 10px 12px;
    }
    details.decision summary { cursor: pointer; font-weight: 650; font-size: 13px; }
    .db-layout {
      height: 100%;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 12px;
      padding: 12px;
      min-height: 0;
    }
    .db-toolbar {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 12px;
      display: grid;
      grid-template-columns: 220px 1fr 180px 120px 120px auto;
      gap: 10px;
      align-items: center;
    }
    .filters {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .filter-row { display: grid; grid-template-columns: 180px 140px 1fr auto; gap: 8px; }
    .table-wrap {
      min-height: 0;
      overflow: auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: var(--shadow);
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }
    th {
      position: sticky;
      top: 0;
      background: #f8fafc;
      z-index: 1;
      white-space: nowrap;
    }
    td { max-width: 420px; overflow-wrap: anywhere; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    .error {
      color: var(--red);
      background: #fee2e2;
      border: 1px solid #fecaca;
      border-radius: 12px;
      padding: 10px;
      font-size: 13px;
    }
    @media (max-width: 1150px) {
      .trace-layout { grid-template-columns: 280px 330px minmax(0, 1fr); }
      .db-toolbar { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div class="title">
        <h1>Alpha Arena DB Viewer</h1>
        <span id="dbPath">正在读取数据库...</span>
      </div>
      <div class="tabs">
        <button id="tabTrace" class="tab active" type="button">Trace 聊天查看</button>
        <button id="tabDb" class="tab" type="button">DB 检索</button>
      </div>
    </header>
    <main>
      <section id="traceView" class="view active">
        <div class="trace-layout">
          <aside class="panel">
            <div class="panel-header">
              <h2>Accounts</h2>
              <input id="accountSearch" placeholder="搜索 account / model" />
            </div>
            <div id="accountsList" class="list"></div>
          </aside>
          <aside class="panel">
            <div class="panel-header">
              <h2>Trace 历史</h2>
              <div class="toolbar">
                <input id="traceSearch" placeholder="搜索 trace / reason / symbol" />
                <select id="traceSort">
                  <option value="started_at:desc">时间倒序</option>
                  <option value="started_at:asc">时间正序</option>
                  <option value="step_count:desc">步骤最多</option>
                  <option value="decision_count:desc">决策最多</option>
                </select>
              </div>
              <div class="toolbar">
                <input id="traceOperation" placeholder="operation" />
                <input id="traceSymbol" placeholder="symbol" />
              </div>
            </div>
            <div id="tracesList" class="list"></div>
            <div class="pager">
              <button id="prevTrace" type="button">上一页</button>
              <span id="tracePager">0 / 0</span>
              <button id="nextTrace" type="button">下一页</button>
            </div>
          </aside>
          <section class="panel">
            <div class="chat-header">
              <div class="chat-title">
                <strong id="chatTraceTitle">请选择一个 trace</strong>
                <span id="chatTraceSub">右侧按 step_number 时间顺序展示完整历史消息</span>
              </div>
              <button id="reloadTrace" type="button">刷新</button>
            </div>
            <div id="chatScroll" class="chat-scroll">
              <div class="empty">选择左侧账号和 trace 后查看完整对话。</div>
            </div>
          </section>
        </div>
      </section>
      <section id="dbView" class="view">
        <div class="db-layout">
          <div class="db-toolbar">
            <select id="tableSelect"></select>
            <input id="dbSearch" placeholder="全表模糊搜索" />
            <select id="sortColumn"></select>
            <select id="sortDir">
              <option value="desc">降序</option>
              <option value="asc">升序</option>
            </select>
            <select id="pageSize">
              <option>50</option>
              <option selected>100</option>
              <option>200</option>
              <option>500</option>
            </select>
            <button id="reloadRows" class="primary" type="button">检索</button>
          </div>
          <div class="filters">
            <div class="toolbar">
              <strong>列筛选</strong>
              <button id="addFilter" type="button">添加筛选</button>
              <button id="clearFilters" type="button">清空筛选</button>
            </div>
            <div id="filterRows"></div>
          </div>
          <div id="tableWrap" class="table-wrap"></div>
          <div class="pager">
            <button id="prevRows" type="button">上一页</button>
            <span id="rowsPager">0 / 0</span>
            <button id="nextRows" type="button">下一页</button>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const state = {
      meta: null,
      accounts: [],
      selectedAccountId: null,
      traces: [],
      traceTotal: 0,
      traceOffset: 0,
      traceLimit: 100,
      selectedTraceId: null,
      selectedTraceAccountId: null,
      dbOffset: 0,
      dbTotal: 0,
      dbFilters: [],
    };

    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
    const fmtTime = (value) => value ? new Date(value).toLocaleString() : '-';
    const shortId = (value) => value ? String(value).slice(0, 8) + '...' : '-';
    const debounce = (fn, ms = 250) => {
      let t = null;
      return (...args) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
      };
    };

    async function api(path) {
      const res = await fetch(path);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `${res.status} ${res.statusText}`);
      }
      return res.json();
    }

    function setTab(name) {
      const trace = name === 'trace';
      $('tabTrace').classList.toggle('active', trace);
      $('tabDb').classList.toggle('active', !trace);
      $('traceView').classList.toggle('active', trace);
      $('dbView').classList.toggle('active', !trace);
    }

    async function init() {
      $('tabTrace').onclick = () => setTab('trace');
      $('tabDb').onclick = () => setTab('db');

      state.meta = await api('/api/meta');
      $('dbPath').textContent = state.meta.db_path;
      state.accounts = await api('/api/accounts');
      renderAccounts();
      initDbControls();

      if (state.accounts.length > 0) {
        state.selectedAccountId = state.accounts[0].id;
        await loadTraces(true);
      }
      await loadRows(true);
    }

    function renderAccounts() {
      const q = $('accountSearch').value.trim().toLowerCase();
      const items = state.accounts.filter((account) => {
        const text = `${account.id} ${account.name} ${account.model || ''}`.toLowerCase();
        return !q || text.includes(q);
      });
      $('accountsList').innerHTML = items.map((account) => `
        <div class="item ${account.id === state.selectedAccountId ? 'active' : ''}" data-account-id="${account.id}">
          <div class="item-title">
            <span>#${esc(account.id)} ${esc(account.name)}</span>
            <span class="badge ${account.is_active === 'true' ? 'green' : 'gray'}">${esc(account.is_active)}</span>
          </div>
          <div class="item-sub">${esc(account.model || account.agent_type || '-')}</div>
          <div class="badges">
            <span class="badge purple">${Number(account.trace_count || 0).toLocaleString()} traces</span>
            <span class="badge gray">${Number(account.step_count || 0).toLocaleString()} steps</span>
            <span class="badge amber">${Number(account.decision_count || 0).toLocaleString()} decisions</span>
          </div>
          <div class="item-sub">最新：${esc(fmtTime(account.last_trace_at || account.last_decision_at))}</div>
        </div>
      `).join('') || '<div class="empty">没有匹配的 account</div>';
      document.querySelectorAll('[data-account-id]').forEach((el) => {
        el.onclick = async () => {
          state.selectedAccountId = Number(el.dataset.accountId);
          state.traceOffset = 0;
          state.selectedTraceId = null;
          renderAccounts();
          await loadTraces(true);
        };
      });
    }

    async function loadTraces(resetOffset = false) {
      if (resetOffset) state.traceOffset = 0;
      const [sortBy, sortDir] = $('traceSort').value.split(':');
      const params = new URLSearchParams({
        limit: String(state.traceLimit),
        offset: String(state.traceOffset),
        q: $('traceSearch').value,
        operation: $('traceOperation').value,
        symbol: $('traceSymbol').value,
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      if (state.selectedAccountId) params.set('account_id', String(state.selectedAccountId));
      const data = await api(`/api/traces?${params.toString()}`);
      state.traces = data.items;
      state.traceTotal = data.total;
      renderTraces();
      if (state.traces.length > 0 && !state.selectedTraceId) {
        await selectTrace(state.traces[0].trace_id, state.traces[0].account_id);
      } else if (state.traces.length === 0) {
        $('chatScroll').innerHTML = '<div class="empty">没有匹配的 trace。</div>';
      }
    }

    function renderTraces() {
      $('tracePager').textContent = `${state.traceTotal === 0 ? 0 : state.traceOffset + 1}-${Math.min(state.traceOffset + state.traceLimit, state.traceTotal)} / ${state.traceTotal}`;
      $('prevTrace').disabled = state.traceOffset <= 0;
      $('nextTrace').disabled = state.traceOffset + state.traceLimit >= state.traceTotal;
      $('tracesList').innerHTML = state.traces.map((trace) => `
        <div class="item ${trace.trace_id === state.selectedTraceId ? 'active' : ''}" data-trace-id="${esc(trace.trace_id)}" data-trace-account-id="${trace.account_id}">
          <div class="item-title">
            <span class="mono">${esc(shortId(trace.trace_id))}</span>
            <span>${esc(fmtTime(trace.started_at))}</span>
          </div>
          <div class="badges">
            <span class="badge purple">${esc(trace.step_count)} steps</span>
            <span class="badge gray">${esc(trace.decision_count)} decisions</span>
            ${trace.latest_operation ? `<span class="badge green">${esc(trace.latest_operation)} ${esc(trace.latest_symbol || '')}</span>` : ''}
          </div>
          <div class="item-sub">${esc(trace.reason_preview || trace.account_name || '')}</div>
        </div>
      `).join('') || '<div class="empty">没有匹配的 trace</div>';
      document.querySelectorAll('[data-trace-id]').forEach((el) => {
        el.onclick = () => selectTrace(el.dataset.traceId, Number(el.dataset.traceAccountId));
      });
    }

    async function selectTrace(traceId, accountId) {
      state.selectedTraceId = traceId;
      state.selectedTraceAccountId = accountId;
      renderTraces();
      const params = new URLSearchParams();
      if (accountId) params.set('account_id', String(accountId));
      const data = await api(`/api/trace/${encodeURIComponent(traceId)}?${params.toString()}`);
      renderTraceDetail(data);
    }

    function splitThink(content) {
      const raw = content || '';
      const thinkParts = [];
      const main = raw.replace(/<think>([\s\S]*?)<\/think>/gi, (_, inner) => {
        if (inner.trim()) thinkParts.push(inner.trim());
        return '';
      }).trim();
      return { main, think: thinkParts.join('\n\n') };
    }

    function renderTraceDetail(data) {
      const account = data.account ? `#${data.account.id} ${data.account.name}` : '';
      $('chatTraceTitle').textContent = `${shortId(data.trace_id)} ${account}`;
      $('chatTraceSub').textContent = `${data.steps.length} steps, ${data.decisions.length} decisions`;
      const decisionsHtml = data.decisions.length ? data.decisions.map((decision) => `
        <details class="decision">
          <summary>${esc(fmtTime(decision.decision_time))} · ${esc(decision.operation)} ${esc(decision.symbol || '')} ${esc(decision.direction || '')}</summary>
          <div class="badges" style="margin: 10px 0;">
            <span class="badge gray">executed: ${esc(decision.executed)}</span>
            <span class="badge gray">balance: ${esc(decision.total_balance)}</span>
            <span class="badge gray">leverage: ${esc(decision.leverage)}</span>
          </div>
          <pre>${esc(decision.reason || '')}</pre>
        </details>
      `).join('') : '';
      const stepsHtml = data.steps.map(renderStep).join('');
      $('chatScroll').innerHTML = `${decisionsHtml}${stepsHtml || '<div class="empty">这个 trace 没有 step 记录。</div>'}`;
      $('chatScroll').scrollTop = $('chatScroll').scrollHeight;
    }

    function renderJsonBlock(title, value) {
      if (value === null || value === undefined || value === '') return '';
      const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
      return `<div class="json-block"><strong>${esc(title)}</strong><pre>${esc(text)}</pre></div>`;
    }

    function renderStep(step) {
      const role = step.role || 'unknown';
      const isUser = role === 'user';
      const isTool = role === 'tool';
      const isMemory = role === 'memory';
      if (role === 'system') {
        return renderSystemStep(step);
      }
      let content = step.content || '';
      let agentName = '';
      const match = content.match(/^\[(.*?)\]/);
      if (match) {
        agentName = match[1];
        content = content.slice(match[0].length).trim();
      }
      const parts = splitThink(content);
      return `
        <div class="message ${isUser ? 'user' : ''} ${isTool ? 'tool' : ''} ${isMemory ? 'memory' : ''}">
          <div class="avatar">${isUser ? 'U' : isTool ? 'T' : isMemory ? 'M' : 'A'}</div>
          <div class="bubble-wrap">
            <div class="meta-line">
              <span>${esc(role.toUpperCase())}</span>
              ${agentName ? `<span class="badge">${esc(agentName)}</span>` : ''}
              <span>#${esc(step.step_number)}</span>
              <span>${esc(fmtTime(step.created_at))}</span>
            </div>
            <div class="bubble">
              ${parts.main ? esc(parts.main) : ''}
              ${parts.think ? `<div class="think-block"><strong>Think</strong><pre>${esc(parts.think)}</pre></div>` : ''}
              ${renderJsonBlock('Tool Calls', step.tool_calls)}
              ${renderJsonBlock('Tool Output', step.tool_output)}
            </div>
          </div>
        </div>
      `;
    }

    function renderSystemStep(step) {
      return `
        <details class="decision">
          <summary>SYSTEM · #${esc(step.step_number)} · ${esc(fmtTime(step.created_at))}</summary>
          <pre>${esc(step.content || '')}</pre>
        </details>
      `;
    }

    function initDbControls() {
      $('tableSelect').innerHTML = state.meta.tables.map((table) => (
        `<option value="${esc(table.name)}">${esc(table.name)} (${Number(table.row_count).toLocaleString()})</option>`
      )).join('');
      $('tableSelect').onchange = () => {
        state.dbOffset = 0;
        state.dbFilters = [];
        refreshColumnControls();
        renderFilterRows();
        loadRows(true);
      };
      refreshColumnControls();
    }

    function currentTableMeta() {
      const name = $('tableSelect').value;
      return state.meta.tables.find((table) => table.name === name);
    }

    function refreshColumnControls() {
      const table = currentTableMeta();
      if (!table) return;
      $('sortColumn').innerHTML = table.columns.map((column) => (
        `<option value="${esc(column.name)}">${esc(column.name)}</option>`
      )).join('');
      const idColumn = table.columns.find((column) => column.pk) || table.columns[0];
      if (idColumn) $('sortColumn').value = idColumn.name;
    }

    function renderFilterRows() {
      const table = currentTableMeta();
      if (!table) return;
      $('filterRows').innerHTML = state.dbFilters.map((filter, index) => `
        <div class="filter-row" data-filter-index="${index}">
          <select data-filter-field="column">
            ${table.columns.map((column) => `<option value="${esc(column.name)}" ${column.name === filter.column ? 'selected' : ''}>${esc(column.name)}</option>`).join('')}
          </select>
          <select data-filter-field="op">
            ${['contains', 'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'is_null', 'not_null'].map((op) => `<option value="${op}" ${op === filter.op ? 'selected' : ''}>${op}</option>`).join('')}
          </select>
          <input data-filter-field="value" value="${esc(filter.value || '')}" placeholder="筛选值" />
          <button type="button" data-remove-filter="${index}">删除</button>
        </div>
      `).join('');
      document.querySelectorAll('[data-filter-field]').forEach((el) => {
        el.onchange = el.oninput = () => {
          const row = el.closest('[data-filter-index]');
          const idx = Number(row.dataset.filterIndex);
          state.dbFilters[idx][el.dataset.filterField] = el.value;
        };
      });
      document.querySelectorAll('[data-remove-filter]').forEach((el) => {
        el.onclick = () => {
          state.dbFilters.splice(Number(el.dataset.removeFilter), 1);
          renderFilterRows();
        };
      });
    }

    async function loadRows(resetOffset = false) {
      if (!state.meta) return;
      if (resetOffset) state.dbOffset = 0;
      const params = new URLSearchParams({
        q: $('dbSearch').value,
        sort_by: $('sortColumn').value,
        sort_dir: $('sortDir').value,
        limit: $('pageSize').value,
        offset: String(state.dbOffset),
        filters: JSON.stringify(state.dbFilters.filter((item) => item.column && item.op)),
      });
      try {
        const data = await api(`/api/db/${encodeURIComponent($('tableSelect').value)}/rows?${params.toString()}`);
        state.dbTotal = data.total;
        renderRows(data);
      } catch (err) {
        $('tableWrap').innerHTML = `<div class="error">${esc(err.message)}</div>`;
      }
    }

    function renderRows(data) {
      $('rowsPager').textContent = `${data.total === 0 ? 0 : data.offset + 1}-${Math.min(data.offset + data.limit, data.total)} / ${data.total}`;
      $('prevRows').disabled = data.offset <= 0;
      $('nextRows').disabled = data.offset + data.limit >= data.total;
      if (!data.items.length) {
        $('tableWrap').innerHTML = '<div class="empty">没有匹配记录</div>';
        return;
      }
      const headers = data.columns.map((column) => `<th>${esc(column.name)}<br><span class="item-sub">${esc(column.type || '')}</span></th>`).join('');
      const rows = data.items.map((row) => `
        <tr>
          ${data.columns.map((column) => {
            const value = row[column.name];
            const text = value === null || value === undefined ? '' : String(value);
            const clipped = text.length > 1000 ? text.slice(0, 1000) + '...' : text;
            return `<td class="mono">${esc(clipped)}</td>`;
          }).join('')}
        </tr>
      `).join('');
      $('tableWrap').innerHTML = `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
    }

    $('accountSearch').oninput = renderAccounts;
    $('traceSearch').oninput = debounce(() => loadTraces(true));
    $('traceOperation').oninput = debounce(() => loadTraces(true));
    $('traceSymbol').oninput = debounce(() => loadTraces(true));
    $('traceSort').onchange = () => loadTraces(true);
    $('prevTrace').onclick = () => { state.traceOffset = Math.max(0, state.traceOffset - state.traceLimit); loadTraces(false); };
    $('nextTrace').onclick = () => { state.traceOffset += state.traceLimit; loadTraces(false); };
    $('reloadTrace').onclick = () => state.selectedTraceId && selectTrace(state.selectedTraceId, state.selectedTraceAccountId);
    $('reloadRows').onclick = () => loadRows(true);
    $('dbSearch').oninput = debounce(() => loadRows(true));
    $('sortColumn').onchange = () => loadRows(true);
    $('sortDir').onchange = () => loadRows(true);
    $('pageSize').onchange = () => loadRows(true);
    $('prevRows').onclick = () => { state.dbOffset = Math.max(0, state.dbOffset - Number($('pageSize').value)); loadRows(false); };
    $('nextRows').onclick = () => { state.dbOffset += Number($('pageSize').value); loadRows(false); };
    $('addFilter').onclick = () => {
      const table = currentTableMeta();
      const firstColumn = table?.columns?.[0]?.name || '';
      state.dbFilters.push({ column: firstColumn, op: 'contains', value: '' });
      renderFilterRows();
    };
    $('clearFilters').onclick = () => {
      state.dbFilters = [];
      renderFilterRows();
      loadRows(true);
    };

    init().catch((err) => {
      document.body.innerHTML = `<div style="padding:24px"><div class="error">${esc(err.message || err)}</div></div>`;
    });
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local read-only viewer for alpha_arena_final.sqlite")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _configure_db_path(Path(args.db))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
