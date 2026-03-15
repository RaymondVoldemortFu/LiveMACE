import argparse
import concurrent.futures
import json
import re
import socket
import threading
import queue
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from database.models import AIDecisionLog, AgentTrace, Account
from services.agent.llm_client import LLMClient


# Model endpoint configs are loaded from a local config file (not hardcoded).
CONFIG: dict[str, str] = {}

METRIC_NAMES = ["metric_a", "metric_b", "metric_c", "metric_d", "metric_e", "metric_f"]


class RunningStats:
    def __init__(self):
        self.count = 0
        self.mean = 0.0
        self.M2 = 0.0

    def update(self, x: float):
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.M2 += delta * delta2

    @property
    def variance(self):
        if self.count < 2:
            return 0.0
        return self.M2 / (self.count - 1)

    def to_dict(self):
        return {
            "count": self.count,
            "mean": self.mean,
            "variance": self.variance,
        }


def load_metric_prompt(metric_name: str) -> str:
    path = Path(__file__).parent / "prompts" / f"{metric_name}.txt"
    return path.read_text(encoding="utf-8")


def load_summary_prompt() -> str:
    path = Path(__file__).parent / "prompts" / "summary_prompt.txt"
    return path.read_text(encoding="utf-8")


def load_env_file(env_path: Path) -> dict[str, str]:
    """Minimal config parser to avoid extra dependency."""
    if not env_path.exists():
        return {}
    data: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            data[key] = val
    return data


def get_cfg(key: str, fallback: Optional[str] = None) -> Optional[str]:
    return CONFIG.get(key, fallback)


def require_cfg(key: str) -> str:
    val = get_cfg(key)
    if not val or val.strip() == "" or val.strip() == "sk-REPLACE_ME":
        raise ValueError(f"Config '{key}' is missing or placeholder. Update evaluation.env.")
    return val


def _extract_first_json_obj(text: str) -> dict[str, Any] | None:
    payload = text.strip()
    try:
        obj = json.loads(payload)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    try:
        for m in re.finditer(r"\{[\s\S]*?\}", payload):
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
    except Exception:
        return None
    return None


def _validate_metric_payload(obj: dict[str, Any], valid_steps: set[int]) -> tuple[dict[str, Any] | None, str]:
    score = obj.get("score")
    if not isinstance(score, int) or score not in {0, 1, 2, 3, 4}:
        return None, "score must be integer in [0,1,2,3,4]"

    evidence_steps = obj.get("evidence_steps")
    if not isinstance(evidence_steps, list) or not evidence_steps:
        return None, "evidence_steps must be non-empty list"
    if any(not isinstance(step, int) for step in evidence_steps):
        return None, "evidence_steps items must be int"
    if any(step not in valid_steps for step in evidence_steps):
        return None, "evidence_steps contains unknown step id"

    rationale = obj.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return None, "rationale must be non-empty string"

    confidence = obj.get("confidence")
    if isinstance(confidence, int):
        confidence = float(confidence)
    if not isinstance(confidence, float) or not (0.0 <= confidence <= 1.0):
        return None, "confidence must be float in [0,1]"

    normalized = {
        "score": score,
        "evidence_steps": sorted(set(evidence_steps)),
        "rationale": rationale.strip(),
        "confidence": float(confidence),
    }
    return normalized, ""


def format_traces_for_llm(traces) -> str:
    return "\n\n".join(f"[Step {t.step_number}]\n{(t.content or '').strip()}" for t in traces)


def summarize_traces(traces) -> str:
    # Summary model comes from config file.
    summary_model = require_cfg("SUMMARY_MODEL")
    summary_api_key = require_cfg("SUMMARY_API_KEY")
    summary_base_url = require_cfg("SUMMARY_BASE_URL")
    llm = LLMClient(
        model=summary_model,
        api_key=summary_api_key,
        base_url=summary_base_url,
    )
    messages = [
        {"role": "system", "content": load_summary_prompt()},
        {"role": "user", "content": format_traces_for_llm(traces)},
    ]
    return llm.call(messages).content


def evaluate_metric(
    summary: str,
    trace_text: str,
    metric_name: str,
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
    valid_steps: list[int],
    max_retries: int = 2,
):
    llm = LLMClient(model=judge_model, api_key=judge_api_key, base_url=judge_base_url)
    valid_steps_set = set(valid_steps)
    valid_steps_hint = ", ".join(str(s) for s in valid_steps)

    messages = [
        {"role": "system", "content": load_metric_prompt(metric_name)},
        {"role": "assistant", "content": f"=== Conversation Summary ===\n{summary}"},
        {"role": "assistant", "content": f"=== Raw Conversation Trace ===\n{trace_text}"},
        {
            "role": "user",
            "content": (
                "Evaluate based on BOTH summary and raw trace. "
                "Return STRICT JSON only with fields: "
                '{"score": <int 0-4>, "evidence_steps": <non-empty int list>, '
                '"rationale": <string>, "confidence": <float 0-1>}.\n'
                f"All evidence_steps must come from these step ids: [{valid_steps_hint}]."
            ),
        },
    ]
    last_response = ""
    last_error = "unknown_error"

    for _ in range(max_retries + 1):
        response = llm.call(messages)
        last_response = response.content or ""

        obj = _extract_first_json_obj(last_response)
        if obj is None:
            last_error = "no_valid_json_object"
        else:
            normalized, err = _validate_metric_payload(obj, valid_steps_set)
            if normalized is not None:
                normalized["raw_response"] = last_response
                return normalized
            last_error = err

        messages.append({"role": "assistant", "content": last_response})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Invalid output: {last_error}. "
                    "Re-output STRICT JSON ONLY with the required schema."
                ),
            }
        )

    return {
        "score": -1,
        "evidence_steps": [],
        "rationale": "",
        "confidence": 0.0,
        "raw_response": last_response,
        "error": f"invalid_judge_output: {last_error}",
    }


def get_trace_ids_by_decisions(db, account_id: int, decision_scope: str):
    query = (
        db.query(AIDecisionLog.trace_id, AIDecisionLog.decision_time)
        .filter(AIDecisionLog.account_id == account_id)
        .filter(AIDecisionLog.trace_id.isnot(None))
        .filter(AIDecisionLog.trace_id != "")
    )
    if decision_scope == "executed":
        query = query.filter(func.lower(AIDecisionLog.executed) == "true")
    query = query.order_by(AIDecisionLog.decision_time.desc())
    rows = query.all()
    return [row[0] for row in reversed(rows)]


def sanitize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")


def resolve_judge_endpoint(
    cli_model: Optional[str],
    cli_api_key: Optional[str],
    cli_base_url: Optional[str],
) -> Tuple[str, str, str]:
    model = cli_model or require_cfg("JUDGE_MODEL")
    api_key = cli_api_key or require_cfg("JUDGE_API_KEY")
    base_url = cli_base_url or require_cfg("JUDGE_BASE_URL")
    return model, api_key, base_url


def get_account_model(db_factory, account_id: int) -> Optional[str]:
    db = db_factory()
    try:
        acc = db.query(Account).filter(Account.id == account_id).first()
        return acc.model if acc else None
    finally:
        db.close()


def load_existing_records(output_file: Path) -> list[dict[str, Any]]:
    if not output_file.exists():
        return []
    try:
        payload = json.loads(output_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict) and r.get("trace_id")]


def upsert_record(records: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    trace_id = record.get("trace_id")
    if not trace_id:
        return records
    replaced = False
    new_records = []
    for r in records:
        if r.get("trace_id") == trace_id:
            new_records.append(record)
            replaced = True
        else:
            new_records.append(r)
    if not replaced:
        new_records.append(record)
    return new_records


def rebuild_metric_stats_from_records(records: list[dict[str, Any]]) -> dict[str, RunningStats]:
    stats = {m: RunningStats() for m in METRIC_NAMES}
    for rec in records:
        evals = rec.get("evaluations") or {}
        if not isinstance(evals, dict):
            continue
        for metric in METRIC_NAMES:
            item = evals.get(metric) or {}
            if not isinstance(item, dict):
                continue
            score = item.get("score")
            if isinstance(score, (int, float)) and float(score) >= 0:
                stats[metric].update(float(score))
    return stats


def build_output_file(db_factory, output_dir: Path, account_id: int, judge_model: str) -> tuple[Path, str]:
    # Use model stored in DB; fallback to account_id label if missing.
    target_model = get_account_model(db_factory, account_id) or f"account_{account_id}"
    output_file = (
        output_dir
        / f"account_{account_id}"
        / f"{sanitize_name(target_model)}__judge_{sanitize_name(judge_model)}.json"
    )
    return output_file, target_model


def build_trace_record(
    db_factory,
    account_id: int,
    trace_id: str,
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
) -> dict[str, Any] | None:
    db = db_factory()
    try:
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.account_id == account_id)
            .filter(AgentTrace.trace_id == trace_id)
            .filter(AgentTrace.role != "tool")
            .filter(AgentTrace.content.isnot(None))
            .order_by(AgentTrace.step_number.asc())
            .all()
        )
        if not traces:
            return None

        summary = summarize_traces(traces)
        trace_text = format_traces_for_llm(traces)
        valid_steps = [int(t.step_number) for t in traces]

        record = {
            "trace_id": trace_id,
            "summary": summary,
            "evaluations": {},
            "created_at": datetime.utcnow().isoformat(),
        }

        with concurrent.futures.ThreadPoolExecutor() as pool:
            futures = {
                metric: pool.submit(
                    evaluate_metric,
                    summary,
                    trace_text,
                    metric,
                    judge_model,
                    judge_api_key,
                    judge_base_url,
                    valid_steps,
                )
                for metric in METRIC_NAMES
            }
            for metric, fut in futures.items():
                try:
                    result = fut.result()
                except Exception as e:
                    record["evaluations"][metric] = {"score": -1, "error": str(e)}
                    continue
                record["evaluations"][metric] = result
        return record
    finally:
        db.close()


def save_payload(
    output_file: Path,
    account_id: int,
    target_model: str,
    judge_model: str,
    decision_scope: str,
    limit_per_account: Optional[int],
    resume_existing: bool,
    records: list[dict[str, Any]],
):
    metric_stats = rebuild_metric_stats_from_records(records)
    if decision_scope == "executed":
        criteria = "ai_decision_logs.executed=true AND trace_id IS NOT NULL"
    else:
        criteria = "ai_decision_logs.trace_id IS NOT NULL (all decisions)"

    payload = {
        "account_id": account_id,
        "target_model": target_model,
        "judge_model": judge_model,
        "source_db": str(DB_PATH),
        "trace_selection": {
            "criteria": criteria,
            "limit_per_account": limit_per_account,
        },
        "resume_existing": resume_existing,
        "records": records,
        "metric_statistics": {k: v.to_dict() for k, v in metric_stats.items()},
        "created_at": datetime.utcnow().isoformat(),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def score_account(
    db_factory,
    account_id: int,
    target_model: str,
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
    output_file: Path,
    limit_per_account: Optional[int],
    decision_scope: str,
    resume_existing: bool,
):
    db = db_factory()
    try:
        trace_ids = get_trace_ids_by_decisions(db, account_id, decision_scope)
    finally:
        db.close()

    records = load_existing_records(output_file) if resume_existing else []
    existing_trace_ids = {r["trace_id"] for r in records if r.get("trace_id")}
    pending_trace_ids = [tid for tid in trace_ids if tid not in existing_trace_ids]

    for trace_id in pending_trace_ids:
        record = build_trace_record(
            db_factory=db_factory,
            account_id=account_id,
            trace_id=trace_id,
            judge_model=judge_model,
            judge_api_key=judge_api_key,
            judge_base_url=judge_base_url,
        )
        if not record:
            continue
        records = upsert_record(records, record)
        print(f"✅ scored account={account_id} trace_id={trace_id}", flush=True)

    payload = save_payload(
        output_file=output_file,
        account_id=account_id,
        target_model=target_model,
        judge_model=judge_model,
        decision_scope=decision_scope,
        limit_per_account=limit_per_account,
        resume_existing=resume_existing,
        records=records,
    )
    print(f"📄 wrote {output_file} (records={len(records)})", flush=True)
    return payload


def score_single_trace(
    db_factory,
    output_dir: Path,
    account_id: int,
    trace_id: str,
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
    decision_scope: str,
    limit_per_account: Optional[int],
):
    output_file, target_model = build_output_file(db_factory, output_dir, account_id, judge_model)
    records = load_existing_records(output_file)
    existing = {r.get("trace_id") for r in records}
    if trace_id in existing:
        return False, f"already_scored trace_id={trace_id}"

    record = build_trace_record(
        db_factory=db_factory,
        account_id=account_id,
        trace_id=trace_id,
        judge_model=judge_model,
        judge_api_key=judge_api_key,
        judge_base_url=judge_base_url,
    )
    if not record:
        return False, f"trace_not_found trace_id={trace_id}"
    records = upsert_record(records, record)
    save_payload(
        output_file=output_file,
        account_id=account_id,
        target_model=target_model,
        judge_model=judge_model,
        decision_scope=decision_scope,
        limit_per_account=limit_per_account,
        resume_existing=True,
        records=records,
    )
    return True, f"scored trace_id={trace_id}"


def run_daemon(
    db_factory,
    output_dir: Path,
    accounts: list[int],
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
    decision_scope: str,
    limit_per_account: Optional[int],
    judge_existing_on_start: bool,
    listen_host: str,
    listen_port: int,
):
    # UDP listener for finish notifications (runs immediately to avoid missing events during backfill).
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((listen_host, listen_port))
    print(f"👂 judge daemon listening on udp://{listen_host}:{listen_port}", flush=True)
    print(f"accounts={accounts}, judge_model={judge_model}, judge_existing_on_start={judge_existing_on_start}", flush=True)

    allowed_accounts = set(accounts)
    event_q: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=10000)

    def receiver_loop():
        while True:
            data, _addr = sock.recvfrom(65535)
            try:
                payload = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            trace_id = payload.get("trace_id")
            account_id = payload.get("account_id")
            event = str(payload.get("event", "")).lower()
            try:
                account_id = int(account_id)
            except Exception:
                continue
            if not trace_id or event not in {"finish", ""}:
                continue
            if account_id not in allowed_accounts:
                continue
            try:
                event_q.put_nowait((account_id, str(trace_id)))
            except queue.Full:
                # Drop oldest to avoid blocking receiver
                try:
                    _ = event_q.get_nowait()
                    event_q.put_nowait((account_id, str(trace_id)))
                except Exception:
                    pass

    threading.Thread(target=receiver_loop, daemon=True).start()

    # Optional backfill on startup (listener already running, so no missed events).
    if judge_existing_on_start:
        for account_id in accounts:
            output_file, target_model = build_output_file(db_factory, output_dir, account_id, judge_model)
            score_account(
                db_factory=db_factory,
                account_id=account_id,
                target_model=target_model,
                judge_model=judge_model,
                judge_api_key=judge_api_key,
                judge_base_url=judge_base_url,
                output_file=output_file,
                limit_per_account=limit_per_account,
                decision_scope=decision_scope,
                resume_existing=True,
            )

    # Process queued events sequentially to keep writes consistent.
    while True:
        account_id, trace_id = event_q.get()
        ok, msg = score_single_trace(
            db_factory=db_factory,
            output_dir=output_dir,
            account_id=account_id,
            trace_id=str(trace_id),
            judge_model=judge_model,
            judge_api_key=judge_api_key,
            judge_base_url=judge_base_url,
            decision_scope=decision_scope,
            limit_per_account=limit_per_account,
        )
        prefix = "✅" if ok else "ℹ️"
        print(f"{prefix} account={account_id} {msg}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db_path",
        type=str,
        default=str(Path(__file__).parent.parent.parent / "data.db"),
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=str(Path(__file__).parent.parent / "eval_results"),
    )
    parser.add_argument("--limit_per_account", type=int, default=None)
    parser.add_argument("--accounts", type=str, required=True) # comma-separated list of account ids, e.g. "1,2,3"
    parser.add_argument("--judge_model", type=str, default=None)
    parser.add_argument("--judge_api_key", type=str, default=None)
    parser.add_argument("--judge_base_url", type=str, default=None)
    parser.add_argument(
        "--mode",
        type=str,
        choices=["batch", "daemon"],
        default="batch",
        help="batch: evaluate existing traces once and exit; daemon: keep listening for finish events",
    )
    parser.add_argument(
        "--decision_scope",
        type=str,
        choices=["executed", "all"],
        default="executed",
        help="executed: only executed=true decisions; all: all decisions with trace_id",
    )
    parser.add_argument(
        "--resume_existing",
        action="store_true",
        help="If output file exists, keep existing records and only score missing trace_ids.",
    )
    parser.add_argument(
        "--judge_existing_on_start",
        action="store_true",
        help="Daemon mode only. Backfill completed traces at startup before listening.",
    )
    parser.add_argument("--listen_host", type=str, default="127.0.0.1")
    parser.add_argument("--listen_port", type=int, default=9010)
    args = parser.parse_args()

    global DB_PATH, CONFIG
    DB_PATH = Path(args.db_path).resolve()
    output_dir = Path(args.output_dir).resolve()
    config_path = Path(__file__).parent / "evaluation.env"
    CONFIG = load_env_file(config_path)

    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=engine)
    judge_model, judge_api_key, judge_base_url = resolve_judge_endpoint(
        args.judge_model, args.judge_api_key, args.judge_base_url
    )

    selected_accounts = []
    for part in args.accounts.split(","):
        part = part.strip()
        if not part:
            continue
        selected_accounts.append(int(part))

    summary = {}
    if args.mode == "daemon":
        run_daemon(
            db_factory=session_local,
            output_dir=output_dir,
            accounts=selected_accounts,
            judge_model=judge_model,
            judge_api_key=judge_api_key,
            judge_base_url=judge_base_url,
            decision_scope=args.decision_scope,
            limit_per_account=args.limit_per_account,
            judge_existing_on_start=args.judge_existing_on_start,
            listen_host=args.listen_host,
            listen_port=args.listen_port,
        )
        return

    for account_id in selected_accounts:
        output_file, target_model = build_output_file(session_local, output_dir, account_id, judge_model)
        payload = score_account(
            db_factory=session_local,
            account_id=account_id,
            target_model=target_model,
            judge_model=judge_model,
            judge_api_key=judge_api_key,
            judge_base_url=judge_base_url,
            output_file=output_file,
            limit_per_account=args.limit_per_account,
            decision_scope=args.decision_scope,
            resume_existing=args.resume_existing,
        )
        summary[f"account_{account_id}"] = payload["metric_statistics"]

    print("\n===== metric summary =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    DB_PATH = Path(".").resolve()
    main()
