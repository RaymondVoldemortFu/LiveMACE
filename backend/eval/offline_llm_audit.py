"""
Offline LLM Audit Script
========================
Scans the database for RuleEvaluationResult records that are missing LLM audit
scores (llm_audit_score IS NULL) and runs them through the same LLMAuditor used
in the live pipeline, then writes the results back.

Run from the backend/ directory:
    uv run python eval/offline_llm_audit.py [OPTIONS]

See README.md in the same directory for full usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# ── Path bootstrap ──────────────────────────────────────────────────────────
# Allow running from backend/ or from the repo root.
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Load .env before importing any app module (mirrors database/connection.py behaviour).
import dotenv
dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True), override=False)


def _bootstrap_database_url_from_cli() -> None:
    """
    Allow overriding DATABASE_URL before database.connection is imported.

    Supported CLI flags (first match wins):
      --database-url <url>
      --database-url=<url>
      --db-path <sqlite_file>
      --db-path=<sqlite_file>
    """
    args = sys.argv[1:]
    database_url: Optional[str] = None
    db_path: Optional[str] = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--database-url="):
            database_url = arg.split("=", 1)[1].strip()
        elif arg == "--database-url" and i + 1 < len(args):
            database_url = args[i + 1].strip()
            i += 1
        elif arg.startswith("--db-path="):
            db_path = arg.split("=", 1)[1].strip()
        elif arg == "--db-path" and i + 1 < len(args):
            db_path = args[i + 1].strip()
            i += 1
        i += 1

    if database_url:
        os.environ["DATABASE_URL"] = database_url
        return

    if db_path:
        abs_path = Path(db_path).expanduser().resolve()
        # Keep sqlite URL style consistent with SQLAlchemy expectations.
        os.environ["DATABASE_URL"] = f"sqlite:///{abs_path.as_posix()}"


_bootstrap_database_url_from_cli()

# ── App imports (after path / env setup) ────────────────────────────────────
from sqlalchemy.orm import Session

from database.connection import SessionLocal, DATABASE_URL
from database.models import RuleEvaluationResult, AgentTrace, Account

from services.agent.llm_client import LLMClient
from services.agent.rule_aware.llm_auditor import LLMAuditor
from services.agent.rule_aware.rule_engine import RuleEngine

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("offline_llm_audit")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_rule_engine(rules_dir: Optional[str] = None) -> RuleEngine:
    """Load RuleEngine from the default config path or a custom directory."""
    if rules_dir is None:
        rules_dir = str(_BACKEND_DIR / "config" / "rules")
    engine = RuleEngine(rule_documents_path=rules_dir)
    loaded = len(engine.get_all_rules())
    if loaded == 0:
        raise RuntimeError(
            f"No rules loaded from '{rules_dir}'. "
            "Check --rules-dir or that config/rules/*.json files exist."
        )
    logger.info("Loaded %d rules from %s", loaded, rules_dir)
    return engine


def _build_llm_client(
    model: Optional[str],
    api_key: Optional[str],
    base_url: Optional[str],
) -> LLMClient:
    """
    Build an LLMClient, falling back to AUDIT_* env vars then API_KEY / BASE_URL.
    Priority (highest first):
        1. CLI arguments --model / --api-key / --base-url
        2. AUDIT_MODEL / AUDIT_API_KEY / AUDIT_BASE_URL env vars
        3. AUDIT_MODEL default "gpt-4.1", API_KEY, BASE_URL env vars
    """
    resolved_model = (
        model
        or os.getenv("AUDIT_MODEL")
        or os.getenv("EVAL_LLM_MODEL")
        or "gpt-4.1"
    )
    resolved_key = (
        api_key
        or os.getenv("AUDIT_API_KEY")
        or os.getenv("EVAL_LLM_API_KEY")
        or os.getenv("API_KEY")
    )
    resolved_url = (
        base_url
        or os.getenv("AUDIT_BASE_URL")
        or os.getenv("EVAL_LLM_BASE_URL")
        or os.getenv("BASE_URL")
    )

    if not resolved_key:
        raise RuntimeError(
            "No API key found. Pass --api-key or set AUDIT_API_KEY / API_KEY in .env."
        )

    logger.info(
        "LLM client: model=%s, base_url=%s",
        resolved_model,
        resolved_url or "(OpenAI official)",
    )
    return LLMClient(model=resolved_model, api_key=resolved_key, base_url=resolved_url)


def _reconstruct_agent_output(db: Session, trace_id: str) -> str:
    """
    Reconstruct the full agent reasoning text from AgentTrace rows.
    Concatenates all assistant-role content in step order — identical to
    the `full_content` / `accumulated_content` that the live auditor receives.
    """
    traces = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.trace_id == trace_id,
            AgentTrace.role == "assistant",
        )
        .order_by(AgentTrace.step_number)
        .all()
    )

    if not traces:
        return ""

    parts = []
    for t in traces:
        if t.content:
            parts.append(t.content)
    return "\n".join(parts)


def _build_market_state(db: Session, record: RuleEvaluationResult) -> dict:
    """
    Best-effort reconstruction of the market_state dict that LLMAuditor expects:
        { "portfolio": { cash, total_equity, positions, account_id }, "prices": {...} }

    - total_equity / cash come from the AccountSnapshot closest to record.ts.
    - prices come from MarketKline (period="5m") for each known symbol, picking
      the closest candle at or before record.ts. The auditor only uses prices as
      display context in the grading prompt — they do not affect score computation.
    """
    import calendar
    from database.models import AccountSnapshot, MarketKline
    from config.settings import AI_TRADING_SYMBOLS

    # ── Portfolio state ───────────────────────────────────────────────────────
    snapshot = (
        db.query(AccountSnapshot)
        .filter(
            AccountSnapshot.account_id == record.account_id,
            AccountSnapshot.ts <= record.ts,
        )
        .order_by(AccountSnapshot.ts.desc())
        .first()
    )

    if snapshot:
        portfolio = {
            "account_id": record.account_id,
            "cash": float(snapshot.cash),
            "total_equity": float(snapshot.total_equity),
            "positions": {},
        }
    else:
        portfolio = {
            "account_id": record.account_id,
            "cash": 0.0,
            "total_equity": 0.0,
            "positions": {},
        }

    # ── Historical prices from MarketKline ────────────────────────────────────
    # Convert record.ts (datetime, assumed UTC) to Unix timestamp for comparison.
    ts_unix = int(calendar.timegm(record.ts.timetuple()))

    prices: dict = {}
    for symbol in AI_TRADING_SYMBOLS:
        kline = (
            db.query(MarketKline)
            .filter(
                MarketKline.symbol == symbol,
                MarketKline.period == "5m",
                MarketKline.timestamp <= ts_unix,
            )
            .order_by(MarketKline.timestamp.desc())
            .first()
        )
        if kline and kline.close_price is not None:
            prices[symbol] = float(kline.close_price)

    return {"portfolio": portfolio, "prices": prices}


def _write_audit_back(
    db: Session,
    record: RuleEvaluationResult,
    llm_audit: dict,
    dry_run: bool,
) -> None:
    """Persist the audit result back into the RuleEvaluationResult row."""
    coverage_data = llm_audit.get("coverage", {})
    conflict_data = llm_audit.get("conflict", {})

    record.llm_audit_score = llm_audit.get("final_normalized_score")
    record.llm_audit_coverage = (
        coverage_data.get("score") if isinstance(coverage_data, dict) else None
    )
    record.llm_audit_conflict = (
        conflict_data.get("score") if isinstance(conflict_data, dict) else None
    )
    record.llm_audit_json = json.dumps(llm_audit, ensure_ascii=False)
    record.s_audit = llm_audit.get("final_normalized_score")

    # Recalculate final_score (mirrors _save_rule_evaluation logic)
    s_rule_sat = record.s_rule_sat
    s_audit = record.s_audit
    if s_rule_sat is not None and s_audit is not None:
        record.final_score = (s_rule_sat + s_audit) / 2.0
    elif s_audit is not None:
        record.final_score = s_audit

    if not dry_run:
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Main processing loop
# ─────────────────────────────────────────────────────────────────────────────

def run_offline_audit(
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    rules_dir: Optional[str] = None,
    account_id: Optional[int] = None,
    limit: int = 0,
    dry_run: bool = False,
    force_reaudit: bool = False,
) -> None:
    """
    Main entry point (also callable from other scripts).

    Args:
        model:          Override LLM model name.
        api_key:        Override API key.
        base_url:       Override API base URL.
        rules_dir:      Override path to rule JSON files.
        account_id:     Restrict to a specific account (None = all accounts).
        limit:          Max records to process per run (0 = unlimited).
        dry_run:        If True, run audits but do NOT write results to DB.
        force_reaudit:  If True, re-audit records that already have scores.
    """
    rule_engine = _build_rule_engine(rules_dir)
    llm_client = _build_llm_client(model, api_key, base_url)
    auditor = LLMAuditor(llm_client)
    rule_documents = rule_engine.format_rules_for_prompt()

    logger.info("Using DATABASE_URL=%s", DATABASE_URL)

    db: Session = SessionLocal()
    try:
        # ── Query records that need auditing ──────────────────────────────────
        q = db.query(RuleEvaluationResult)
        if account_id is not None:
            q = q.filter(RuleEvaluationResult.account_id == account_id)
        if not force_reaudit:
            q = q.filter(RuleEvaluationResult.llm_audit_score.is_(None))
        q = q.order_by(RuleEvaluationResult.ts.asc())
        if limit > 0:
            q = q.limit(limit)

        records = q.all()
        total = len(records)
        logger.info(
            "Found %d record(s) to audit%s.",
            total,
            " (dry-run, no DB writes)" if dry_run else "",
        )

        if total == 0:
            logger.info("Nothing to do.")
            return

        success = 0
        failed = 0

        for i, record in enumerate(records, 1):
            trace_id = record.trace_id
            logger.info(
                "[%d/%d] Processing RuleEvaluationResult id=%d, trace_id=%s, account_id=%d",
                i, total, record.id, trace_id, record.account_id,
            )

            # ── Rebuild agent output from AgentTrace ──────────────────────────
            if not trace_id:
                logger.warning(
                    "  Skipping id=%d: no trace_id, cannot reconstruct agent output.",
                    record.id,
                )
                failed += 1
                continue

            agent_output = _reconstruct_agent_output(db, trace_id)
            if not agent_output.strip():
                logger.warning(
                    "  Skipping id=%d: AgentTrace has no assistant content for trace_id=%s.",
                    record.id, trace_id,
                )
                failed += 1
                continue

            # ── Rebuild market state ──────────────────────────────────────────
            market_state = _build_market_state(db, record)

            # ── Run LLM audit ─────────────────────────────────────────────────
            try:
                llm_audit = auditor.audit_agent_reasoning(
                    rules=rule_documents,
                    market_state=market_state,
                    agent_output=agent_output,
                )
            except Exception as exc:
                logger.error("  LLM audit API call failed for id=%d: %s", record.id, exc)
                failed += 1
                continue

            if "error" in llm_audit:
                logger.warning(
                    "  Audit returned error for id=%d: %s",
                    record.id, llm_audit["error"],
                )
                failed += 1
                continue

            # ── Persist results ───────────────────────────────────────────────
            _write_audit_back(db, record, llm_audit, dry_run)

            score = llm_audit.get("final_normalized_score", 0)
            cov = (llm_audit.get("coverage") or {}).get("score", "?")
            con = (llm_audit.get("conflict") or {}).get("score", "?")
            logger.info(
                "  Done id=%d: final=%.3f  coverage=%s/10  conflict=%s/10%s",
                record.id, score, cov, con,
                "  [dry-run, not saved]" if dry_run else "",
            )
            success += 1

        logger.info(
            "Offline audit complete: %d succeeded, %d failed / skipped (total %d).",
            success, failed, total,
        )

    finally:
        db.close()
        llm_client.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="offline_llm_audit",
        description=(
            "Offline LLM audit: find RuleEvaluationResult rows without LLM audit "
            "scores and run the same LLMAuditor used in the live pipeline."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        dest="database_url",
        help=(
            "Override DATABASE_URL for this run. Example: "
            "sqlite:///./alpha_arena.sqlite"
        ),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        dest="db_path",
        help=(
            "SQLite file path shortcut for this run. Example: ./alpha_arena.sqlite"
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name (overrides AUDIT_MODEL env var). Default: gpt-4.1",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        dest="api_key",
        help="API key (overrides AUDIT_API_KEY / API_KEY env vars).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        dest="base_url",
        help="API base URL (overrides AUDIT_BASE_URL / BASE_URL env vars).",
    )
    parser.add_argument(
        "--rules-dir",
        default=None,
        dest="rules_dir",
        help="Path to rule JSON files directory. Default: backend/config/rules/",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        default=None,
        dest="account_id",
        help="Restrict to a single account ID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max number of records to process in one run (0 = unlimited).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Run audits but do NOT write results back to the database.",
    )
    parser.add_argument(
        "--force-reaudit",
        action="store_true",
        dest="force_reaudit",
        help="Re-audit records that already have llm_audit_score (overwrite).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_offline_audit(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        rules_dir=args.rules_dir,
        account_id=args.account_id,
        limit=args.limit,
        dry_run=args.dry_run,
        force_reaudit=args.force_reaudit,
    )
