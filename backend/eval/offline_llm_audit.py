"""
Offline LLM Audit Script
========================
Scans the database for RuleEvaluationResult records that are missing LLM audit
scores (llm_audit_score IS NULL) and runs them through the same LLMAuditor used
in the live pipeline. By default it writes the results back; with --dry-run or
--no-write-db it writes JSON/CSV evaluation outputs without mutating the DB.

Run from the backend/ directory:
    uv run python eval/offline_llm_audit.py [OPTIONS]

See README.md in the same directory for full usage.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    extra_body_json: Optional[str] = None,
    disable_thinking: bool = False,
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

    extra_body = _build_audit_extra_body(
        extra_body_json=extra_body_json,
        disable_thinking=disable_thinking,
    )

    logger.info(
        "LLM client: model=%s, base_url=%s%s",
        resolved_model,
        resolved_url or "(OpenAI official)",
        f", extra_body_keys={sorted(extra_body.keys())}" if extra_body else "",
    )
    return LLMClient(
        model=resolved_model,
        api_key=resolved_key,
        base_url=resolved_url,
        extra_body=extra_body,
    )


def _parse_optional_bool(value: Optional[str]) -> Optional[bool]:
    """Parse common env-style boolean values; return None for unset/unknown."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _build_audit_extra_body(
    *,
    extra_body_json: Optional[str] = None,
    disable_thinking: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Build provider-specific OpenAI-compatible request body extensions.

    For Qwen3/Qwen3.5 thinking models, DashScope-style compatible endpoints use
    extra_body={"enable_thinking": false}. vLLM-style endpoints may instead
    require extra_body={"chat_template_kwargs": {"enable_thinking": false}}.
    """
    raw_extra_body = (
        extra_body_json
        or os.getenv("AUDIT_EXTRA_BODY_JSON")
        or os.getenv("EVAL_LLM_EXTRA_BODY_JSON")
    )
    extra_body: Dict[str, Any] = {}
    if raw_extra_body:
        try:
            parsed = json.loads(raw_extra_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid extra body JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("--extra-body-json / AUDIT_EXTRA_BODY_JSON must be a JSON object")
        extra_body.update(parsed)

    env_disable = _parse_optional_bool(os.getenv("AUDIT_DISABLE_THINKING"))
    env_enable = _parse_optional_bool(
        os.getenv("AUDIT_ENABLE_THINKING") or os.getenv("EVAL_LLM_ENABLE_THINKING")
    )
    should_disable_thinking = (
        disable_thinking
        or env_disable is True
        or env_enable is False
    )

    if should_disable_thinking:
        style = (
            os.getenv("AUDIT_THINKING_PARAM_STYLE")
            or os.getenv("EVAL_LLM_THINKING_PARAM_STYLE")
            or "top_level"
        ).strip().lower().replace("-", "_")
        if style in {"vllm", "chat_template", "chat_template_kwargs"}:
            chat_template_kwargs = extra_body.setdefault("chat_template_kwargs", {})
            if not isinstance(chat_template_kwargs, dict):
                raise ValueError("extra_body.chat_template_kwargs must be a JSON object")
            chat_template_kwargs["enable_thinking"] = False
        elif style in {"both", "all"}:
            extra_body["enable_thinking"] = False
            chat_template_kwargs = extra_body.setdefault("chat_template_kwargs", {})
            if not isinstance(chat_template_kwargs, dict):
                raise ValueError("extra_body.chat_template_kwargs must be a JSON object")
            chat_template_kwargs["enable_thinking"] = False
        else:
            # Default: DashScope/Qwen OpenAI-compatible style.
            extra_body["enable_thinking"] = False

    return extra_body or None


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
        {
          "portfolio": {
            cash, total_equity, positions_value, positions, positions_count,
            account_id, snapshot_ts
          },
          "prices": {...},
          "price_timestamps": {...}
        }

    - total_equity / cash come from the AccountSnapshot closest to record.ts.
    - AccountSnapshot stores aggregate position value, but not historical per-symbol
      positions. We therefore do NOT pretend that positions={} means zero positions;
      positions_count is left as None and positions_source explains the limitation.
    - prices come from MarketKline (period="5m") for each tradable crypto/US symbol,
      picking the closest candle at or before record.ts. The auditor only uses prices
      as display context in the grading prompt — they do not affect score computation.
    """
    import calendar
    from database.models import AccountSnapshot, MarketKline
    from config.settings import AI_TRADING_SYMBOLS

    audit_us_symbols = [
        "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
        "PG", "JNJ", "UNH", "JPM", "V", "BA", "XOM",
        "NEE", "AMT", "PLD", "LIN",
    ]
    audit_price_symbols = list(dict.fromkeys(list(AI_TRADING_SYMBOLS) + audit_us_symbols))

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
            "positions_value": float(snapshot.positions_value),
            "positions": {},
            "positions_count": None,
            "positions_source": (
                "Historical per-symbol positions are not stored in AccountSnapshot; "
                "using aggregate positions_value only."
            ),
            "snapshot_ts": snapshot.ts.isoformat() if snapshot.ts else None,
        }
    else:
        portfolio = {
            "account_id": record.account_id,
            "cash": 0.0,
            "total_equity": 0.0,
            "positions_value": 0.0,
            "positions": {},
            "positions_count": None,
            "positions_source": "No AccountSnapshot found at or before evaluation timestamp.",
            "snapshot_ts": None,
        }

    # ── Historical prices from MarketKline ────────────────────────────────────
    # Convert record.ts (datetime, assumed UTC) to Unix timestamp for comparison.
    ts_unix = int(calendar.timegm(record.ts.timetuple()))

    prices: dict = {}
    price_timestamps: dict = {}
    for symbol in audit_price_symbols:
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
            price_timestamps[symbol] = {
                "datetime": kline.datetime_str,
                "timestamp": int(kline.timestamp),
                "market": kline.market,
                "period": kline.period,
            }

    return {"portfolio": portfolio, "prices": prices, "price_timestamps": price_timestamps}


def _write_audit_back(
    db: Session,
    record: RuleEvaluationResult,
    llm_audit: dict,
) -> None:
    """Persist the audit result back into the RuleEvaluationResult row."""
    fields = _derive_audit_fields(record, llm_audit)

    record.llm_audit_score = fields["llm_audit_score"]
    record.llm_audit_coverage = fields["llm_audit_coverage"]
    record.llm_audit_conflict = fields["llm_audit_conflict"]
    record.llm_audit_json = fields["llm_audit_json"]
    record.s_audit = fields["s_audit"]
    record.final_score = fields["final_score"]

    db.commit()


def _derive_audit_fields(record: RuleEvaluationResult, llm_audit: dict) -> Dict[str, Any]:
    """
    Compute the DB fields that would be written for an LLM audit result.

    This is intentionally side-effect free so --dry-run / --no-write-db can
    still produce complete result files without mutating the SQLAlchemy row.
    """
    coverage_data = llm_audit.get("coverage", {})
    conflict_data = llm_audit.get("conflict", {})

    llm_audit_score = llm_audit.get("final_normalized_score")
    llm_audit_coverage = (
        coverage_data.get("score") if isinstance(coverage_data, dict) else None
    )
    llm_audit_conflict = (
        conflict_data.get("score") if isinstance(conflict_data, dict) else None
    )
    s_audit = llm_audit_score

    # Recalculate final_score (mirrors _save_rule_evaluation logic)
    s_rule_sat = record.s_rule_sat
    final_score = None
    if s_rule_sat is not None and s_audit is not None:
        final_score = (float(s_rule_sat) + float(s_audit)) / 2.0
    elif s_audit is not None:
        final_score = float(s_audit)

    return {
        "llm_audit_score": llm_audit_score,
        "llm_audit_coverage": llm_audit_coverage,
        "llm_audit_conflict": llm_audit_conflict,
        "llm_audit_json": json.dumps(llm_audit, ensure_ascii=False),
        "s_audit": s_audit,
        "final_score": final_score,
    }


def _build_result_entry(
    record: RuleEvaluationResult,
    llm_audit: dict,
    account_map: Dict[int, Account],
) -> Dict[str, Any]:
    """Build a serializable per-record output entry."""
    fields = _derive_audit_fields(record, llm_audit)
    account = account_map.get(record.account_id)
    return {
        "record_id": record.id,
        "trace_id": record.trace_id,
        "account_id": record.account_id,
        "account_name": account.name if account else None,
        "model": account.model if account else None,
        "ts": record.ts.isoformat() if record.ts else None,
        "gate_pass": record.gate_pass,
        "s_rule_sat": float(record.s_rule_sat) if record.s_rule_sat is not None else None,
        "original_llm_audit_score": record.llm_audit_score,
        "original_coverage": record.llm_audit_coverage,
        "original_conflict": record.llm_audit_conflict,
        "new_coverage": fields["llm_audit_coverage"],
        "new_conflict": fields["llm_audit_conflict"],
        "new_final_normalized_score": fields["llm_audit_score"],
        "derived_final_score": fields["final_score"],
        "audit_detail": llm_audit,
    }


def _compute_summary(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate per-account/model statistics from output entries."""
    grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for entry in results:
        grouped[int(entry["account_id"])].append(entry)

    def _avg(vals: List[float]) -> Optional[float]:
        return round(sum(vals) / len(vals), 4) if vals else None

    summary: List[Dict[str, Any]] = []
    for account_id, entries in sorted(grouped.items()):
        first = entries[0]
        llm_scores = [
            float(e["new_final_normalized_score"])
            for e in entries
            if e.get("new_final_normalized_score") is not None
        ]
        coverages = [
            float(e["new_coverage"])
            for e in entries
            if e.get("new_coverage") is not None
        ]
        conflicts = [
            float(e["new_conflict"])
            for e in entries
            if e.get("new_conflict") is not None
        ]
        rule_sats = [
            float(e["s_rule_sat"])
            for e in entries
            if e.get("s_rule_sat") is not None
        ]
        finals = [
            float(e["derived_final_score"])
            for e in entries
            if e.get("derived_final_score") is not None
        ]

        summary.append({
            "account_id": account_id,
            "account_name": first.get("account_name"),
            "model": first.get("model"),
            "count": len(entries),
            "avg_new_coverage": _avg(coverages),
            "avg_new_conflict": _avg(conflicts),
            "avg_new_llm_audit": _avg(llm_scores),
            "avg_s_rule_sat": _avg(rule_sats),
            "avg_derived_final": _avg(finals),
        })
    return summary


def _write_output_files(
    output_dir: Path,
    results: List[Dict[str, Any]],
    summary: List[Dict[str, Any]],
    *,
    success: int,
    failed: int,
    write_db: bool,
) -> None:
    """Write JSON/CSV audit outputs for offline analysis."""
    output_dir.mkdir(parents=True, exist_ok=True)

    results_path = output_dir / "results.json"
    with results_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "meta": {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "audit_variant": "standard",
                    "total_processed": success,
                    "total_failed": failed,
                    "write_db": write_db,
                    "database_url": DATABASE_URL,
                },
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("Results JSON: %s", results_path)

    summary_path = output_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("Summary JSON: %s", summary_path)

    summary_fields = [
        "account_id", "account_name", "model", "count",
        "avg_new_coverage", "avg_new_conflict",
        "avg_new_llm_audit", "avg_s_rule_sat", "avg_derived_final",
    ]
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=summary_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summary)

    results_fields = [
        "record_id", "trace_id", "account_id", "account_name", "model", "ts",
        "gate_pass", "s_rule_sat",
        "original_llm_audit_score", "original_coverage", "original_conflict",
        "new_coverage", "new_conflict",
        "new_final_normalized_score", "derived_final_score",
    ]
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


# ─────────────────────────────────────────────────────────────────────────────
# Main processing loop
# ─────────────────────────────────────────────────────────────────────────────

def run_offline_audit(
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    extra_body_json: Optional[str] = None,
    disable_thinking: bool = False,
    rules_dir: Optional[str] = None,
    account_id: Optional[int] = None,
    limit: int = 0,
    dry_run: bool = False,
    force_reaudit: bool = False,
    output_dir: Optional[str] = None,
) -> None:
    """
    Main entry point (also callable from other scripts).

    Args:
        model:          Override LLM model name.
        api_key:        Override API key.
        base_url:       Override API base URL.
        extra_body_json:Provider-specific extra JSON body for chat.completions.
        disable_thinking:Disable Qwen-style thinking mode via extra_body.
        rules_dir:      Override path to rule JSON files.
        account_id:     Restrict to a specific account (None = all accounts).
        limit:          Max records to process per run (0 = unlimited).
        dry_run:        If True, run audits but do NOT write results to DB.
        force_reaudit:  If True, re-audit records that already have scores.
        output_dir:     Optional directory for JSON/CSV outputs. If dry_run=True
                        and output_dir is omitted, a timestamped directory is
                        created under eval_output/offline_llm_audit_<ts>/.
    """
    rule_engine = _build_rule_engine(rules_dir)
    llm_client = _build_llm_client(
        model,
        api_key,
        base_url,
        extra_body_json=extra_body_json,
        disable_thinking=disable_thinking,
    )
    auditor = LLMAuditor(llm_client)
    rule_documents = rule_engine.format_rules_for_prompt()

    logger.info("Using DATABASE_URL=%s", DATABASE_URL)

    write_db = not dry_run
    out_path: Optional[Path] = None
    if output_dir is not None:
        out_path = Path(output_dir)
    elif dry_run:
        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = _THIS_DIR / "eval_output" / f"offline_llm_audit_{ts_str}"

    if out_path is not None:
        logger.info("Output directory: %s", out_path)
    if not write_db:
        logger.info("DB write disabled: audit results will only be written to output files/logs.")

    db: Session = SessionLocal()
    results_out: List[Dict[str, Any]] = []
    try:
        account_map: Dict[int, Account] = {a.id: a for a in db.query(Account).all()}

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
            results_out.append(_build_result_entry(record, llm_audit, account_map))

            if write_db:
                _write_audit_back(db, record, llm_audit)

            score = llm_audit.get("final_normalized_score", 0)
            cov = (llm_audit.get("coverage") or {}).get("score", "?")
            con = (llm_audit.get("conflict") or {}).get("score", "?")
            logger.info(
                "  Done id=%d: final=%.3f  coverage=%s/10  conflict=%s/10%s",
                record.id, score, cov, con,
                "  [not written to DB]" if not write_db else "",
            )
            success += 1

        if out_path is not None:
            summary = _compute_summary(results_out)
            _write_output_files(
                out_path,
                results_out,
                summary,
                success=success,
                failed=failed,
                write_db=write_db,
            )

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
        "--disable-thinking",
        action="store_true",
        dest="disable_thinking",
        help=(
            "Disable Qwen-style thinking mode by adding extra_body "
            '{"enable_thinking": false}. Equivalent env: AUDIT_ENABLE_THINKING=False.'
        ),
    )
    parser.add_argument(
        "--extra-body-json",
        default=None,
        dest="extra_body_json",
        help=(
            "Provider-specific JSON object merged into chat.completions.create. "
            "Example: '{\"enable_thinking\": false}'"
        ),
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
        help=(
            "Run audits but do NOT write results back to the database. "
            "A timestamped output directory is created unless --output-dir is set."
        ),
    )
    parser.add_argument(
        "--no-write-db",
        "--output-only",
        action="store_true",
        dest="no_write_db",
        help="Alias for --dry-run: evaluate and write JSON/CSV outputs only.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        dest="output_dir",
        help=(
            "Optional directory for results.json/results.csv/summary.json/summary.csv. "
            "If omitted, files are only written automatically for --dry-run/--no-write-db."
        ),
    )
    parser.add_argument(
        "--force-reaudit",
        action="store_true",
        dest="force_reaudit",
        help=(
            "Re-audit records that already have llm_audit_score. If DB writing "
            "is enabled, existing llm_audit_* fields are overwritten."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    dry_run = args.dry_run or args.no_write_db
    run_offline_audit(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        extra_body_json=args.extra_body_json,
        disable_thinking=args.disable_thinking,
        rules_dir=args.rules_dir,
        account_id=args.account_id,
        limit=args.limit,
        dry_run=dry_run,
        force_reaudit=args.force_reaudit,
        output_dir=args.output_dir,
    )
