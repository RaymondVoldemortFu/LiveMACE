#!/usr/bin/env python3
from __future__ import annotations

import argparse
import threading
import csv
import json
import os
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent.llm_client import LLMClient
from services.agent.tool_selector import META_TOOL_NAME, REQUIRED_TOOL_NAMES, select_tools_with_llm
from services.agent.tools import ToolRegistry
from services.agent.env_wrapper import register_default_tools
from services.agent.public_apis_registry import register_public_api_tools
from services.agent.history_tool import HistoryTool
from services.evaluation.llm_tool_judge import compute_routing_quality_from_steps


@dataclass
class SelectorRecord:
    record_id: str
    account_id: int
    account_name: str
    model: str
    base_url: str
    trace_id: str
    selector_step: int
    original_selected_tools: List[str]
    context_messages: List[Dict[str, Any]]


def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _parse_tool_calls(raw_tool_calls: str | None) -> List[Dict[str, Any]]:
    if not raw_tool_calls:
        return []
    parsed = _safe_json_loads(raw_tool_calls)
    if not isinstance(parsed, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in parsed:
        obj = _safe_json_loads(item)
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _contains_select_tools_call(raw_tool_calls: str | None) -> bool:
    for call in _parse_tool_calls(raw_tool_calls):
        block = call.get("function") if isinstance(call.get("function"), dict) else call
        name = block.get("name")
        if isinstance(name, str) and name == META_TOOL_NAME:
            return True
    return False


def _normalize_selected_tools(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        name = str(item).strip()
        if not name:
            continue
        out.append(name)
    return out


def _build_context_messages(rows_before_selector_tool: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for row in rows_before_selector_tool:
        role = (row["role"] or "").strip()
        content: Any = row["content"]
        if role == "tool":
            content = _safe_json_loads(row["tool_output"]) if row["tool_output"] else content
        messages.append(
            {
                "role": role or "unknown",
                "content": content,
                "name": None,
            }
        )
    return messages


def _load_selector_records(db_path: Path) -> Dict[str, List[SelectorRecord]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, name, model, base_url
        FROM accounts
        WHERE account_type='AI'
          AND lower(tool_routing_enabled)='true'
          AND model IS NOT NULL
          AND trim(model)!=''
        ORDER BY model, id
        """
    )
    account_rows = cur.fetchall()
    account_ids = [int(r["id"]) for r in account_rows]
    if not account_ids:
        raise RuntimeError("未找到启用 tool-routing 的 AI 账号。")

    traces_by_account: Dict[int, Dict[str, List[sqlite3.Row]]] = defaultdict(lambda: defaultdict(list))
    placeholders = ",".join("?" for _ in account_ids)
    cur.execute(
        f"""
        SELECT account_id, trace_id, step_number, role, content, tool_calls, tool_output
        FROM agent_traces
        WHERE account_id IN ({placeholders})
        ORDER BY account_id, trace_id, step_number
        """,
        account_ids,
    )
    for row in cur.fetchall():
        traces_by_account[int(row["account_id"])][row["trace_id"]].append(row)

    records_by_model: Dict[str, List[SelectorRecord]] = defaultdict(list)
    for account in account_rows:
        account_id = int(account["id"])
        model = str(account["model"]).strip()
        account_name = str(account["name"]).strip()
        base_url = str(account["base_url"] or "").strip()

        for trace_id, rows in traces_by_account[account_id].items():
            for idx, row in enumerate(rows):
                if (row["role"] or "").strip() != "tool":
                    continue
                payload = _safe_json_loads(row["tool_output"] or row["content"])
                if not isinstance(payload, dict):
                    continue
                selected_tools = _normalize_selected_tools(payload.get("selected_tools"))
                if not selected_tools:
                    continue

                has_prior_selector = False
                for prev in reversed(rows[:idx]):
                    if (prev["role"] or "").strip() != "assistant":
                        continue
                    if _contains_select_tools_call(prev["tool_calls"]):
                        has_prior_selector = True
                        break
                if not has_prior_selector:
                    continue

                selector_step = int(row["step_number"])
                context_rows = [r for r in rows if int(r["step_number"]) < selector_step]
                context_messages = _build_context_messages(context_rows)

                record = SelectorRecord(
                    record_id=f"{trace_id}:{selector_step}",
                    account_id=account_id,
                    account_name=account_name,
                    model=model,
                    base_url=base_url,
                    trace_id=trace_id,
                    selector_step=selector_step,
                    original_selected_tools=selected_tools,
                    context_messages=context_messages,
                )
                records_by_model[model].append(record)

    conn.close()
    return records_by_model


def _build_registry(db_session, account_id: int) -> ToolRegistry:
    registry = ToolRegistry()
    register_default_tools(registry, db_session, account_id)
    register_public_api_tools(registry)
    registry.register(HistoryTool(db_session, account_id))
    return registry


def _prepare_tool_schemas(registry: ToolRegistry) -> List[Dict[str, Any]]:
    tool_schemas = [
        t for t in registry.openai_tools_all if t.get("function", {}).get("name") != META_TOOL_NAME
    ]
    tool_schemas.sort(key=lambda x: x.get("function", {}).get("name") or "")
    return tool_schemas


def _score_selected_tools(selected_tools: List[str]) -> float:
    result = compute_routing_quality_from_steps(
        [
            {
                "role": "tool",
                "tool_output": {
                    "selected_tools": selected_tools,
                },
            }
        ]
    )
    return float(result["score"])


def _mean(values: Iterable[float]) -> float:
    data = list(values)
    if not data:
        return 0.0
    return float(statistics.mean(data))


def _is_target_model(model: str) -> bool:
    model_l = (model or "").strip().lower()
    return ("gpt" in model_l) or ("deepseek" in model_l)


def _calculate_min_k_with_extra(registry: ToolRegistry, min_extra: int) -> int:
    required_present = [name for name in REQUIRED_TOOL_NAMES if name in registry.tools]
    important = [
        name
        for name, tool in registry.tools.items()
        if (tool.metadata or {}).get("tier") == "important"
    ]
    unique_names = set(required_present + important)
    return len(unique_names) + min_extra


def _arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于真实 tool-selector 记录，分析 TOOL_SELECTOR_MIN_EXTRA (1-10) 对 Routing Quality Score 的影响。"
    )
    parser.add_argument(
        "--db-path",
        default=str(BACKEND_DIR / "alpha_arena.sqlite"),
        help="SQLite 数据库路径（默认 backend/alpha_arena.sqlite）",
    )
    parser.add_argument("--sample-size", type=int, default=5, help="每个模型采样记录数，默认 5")
    parser.add_argument("--seed", type=int, default=42, help="随机种子，默认 42")
    parser.add_argument("--min-extra-start", type=int, default=1, help="扫描起始值，默认 1")
    parser.add_argument("--min-extra-end", type=int, default=10, help="扫描结束值，默认 10（含）")
    parser.add_argument("--workers", type=int, default=5, help="并发线程数，默认 5")
    parser.add_argument(
        "--output-dir",
        default=str(SCRIPT_DIR / "results"),
        help="输出目录（默认 backend/script/results）",
    )
    parser.add_argument(
        "--sample-only",
        action="store_true",
        help="仅采样并导出测试集，不进行 LLM 重放评测。",
    )
    return parser


def main() -> None:
    args = _arg_parser().parse_args()
    db_path = Path(args.db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")
    if args.sample_size <= 0:
        raise ValueError("--sample-size 必须 > 0")
    if args.workers <= 0:
        raise ValueError("--workers 必须 > 0")
    if args.min_extra_start > args.min_extra_end:
        raise ValueError("--min-extra-start 必须 <= --min-extra-end")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    print(f"[1/4] 从 {db_path} 加载 tool-selector 真实记录...")
    all_records_by_model = _load_selector_records(db_path)
    records_by_model = {
        model: rows
        for model, rows in all_records_by_model.items()
        if _is_target_model(model)
    }
    if not records_by_model:
        raise RuntimeError("没有找到可用的 gpt/deepseek tool-selector 真实记录。")

    rng = random.Random(args.seed)
    sampled_records: List[SelectorRecord] = []
    for model, records in sorted(records_by_model.items()):
        if len(records) < args.sample_size:
            raise RuntimeError(
                f"模型 {model} 可用记录仅 {len(records)} 条，少于要求的 {args.sample_size} 条。"
            )
        sampled = rng.sample(records, args.sample_size)
        sampled_records.extend(sampled)
        print(f"  - model={model} available={len(records)} sampled={len(sampled)}")

    testset_payload = {
        "generated_at_utc": run_id,
        "db_path": str(db_path),
        "seed": args.seed,
        "sample_size_per_model": args.sample_size,
        "models": sorted({r.model for r in sampled_records}),
        "records": [
            {
                "record_id": r.record_id,
                "account_id": r.account_id,
                "account_name": r.account_name,
                "model": r.model,
                "trace_id": r.trace_id,
                "selector_step": r.selector_step,
                "original_selected_tools": r.original_selected_tools,
                "context_messages": r.context_messages,
            }
            for r in sampled_records
        ],
    }
    testset_path = output_dir / f"tool_selector_testset_{run_id}.json"
    testset_path.write_text(json.dumps(testset_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[2/4] 测试集已写入: {testset_path}")

    if args.sample_only:
        print("已启用 --sample-only，跳过重放评测。")
        return

    api_key = (os.getenv("API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("缺少 API_KEY 环境变量，无法重放 tool-selector。")
    default_base_url = (os.getenv("BASE_URL") or "").strip()

    print("[3/4] 进行 TOOL_SELECTOR_MIN_EXTRA 扫描重放...")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    by_account_registry: Dict[int, Tuple[ToolRegistry, List[Dict[str, Any]]]] = {}

    try:
        for account_id in {r.account_id for r in sampled_records}:
            registry = _build_registry(session, account_id)
            by_account_registry[account_id] = (registry, _prepare_tool_schemas(registry))

        model_base_url_map: Dict[str, str] = {}
        for model in {r.model for r in sampled_records}:
            model_base_url = ""
            for rec in sampled_records:
                if rec.model == model and rec.base_url:
                    model_base_url = rec.base_url
                    break
            model_base_url_map[model] = model_base_url or default_base_url or ""

        all_scores: List[Dict[str, Any]] = []
        model_extra_scores: Dict[Tuple[str, int], List[float]] = defaultdict(list)
        overall_extra_scores: Dict[int, List[float]] = defaultdict(list)
        sampled_records_sorted = sorted(sampled_records, key=lambda x: (x.model, x.record_id))
        total_jobs = len(sampled_records_sorted) * (args.min_extra_end - args.min_extra_start + 1)
        print(f"  - 并发执行: workers={args.workers}, total_jobs={total_jobs}")
        progress_lock = threading.Lock()
        current_job = 0

        jobs: List[Tuple[SelectorRecord, int]] = []
        baseline_score_map: Dict[str, float] = {}
        for rec in sampled_records_sorted:
            baseline_score_map[rec.record_id] = _score_selected_tools(rec.original_selected_tools)
            for min_extra in range(args.min_extra_start, args.min_extra_end + 1):
                jobs.append((rec, min_extra))

        def _run_one(job: Tuple[SelectorRecord, int]) -> Dict[str, Any]:
            rec, min_extra = job
            registry, tool_schemas = by_account_registry[rec.account_id]
            min_k = _calculate_min_k_with_extra(registry, min_extra)
            llm = LLMClient(
                model=rec.model,
                api_key=api_key,
                base_url=model_base_url_map.get(rec.model) or None,
            )
            try:
                replay = select_tools_with_llm(
                    llm=llm,
                    messages=rec.context_messages,
                    tool_schemas=tool_schemas,
                    min_k=min_k,
                    agent_name=f"min_extra_{min_extra}_analysis",
                )
            finally:
                llm.close()
            selected_tools = _normalize_selected_tools(replay.get("selected_tools"))
            score = _score_selected_tools(selected_tools)
            baseline_score = baseline_score_map[rec.record_id]
            return {
                "record_id": rec.record_id,
                "model": rec.model,
                "account_id": rec.account_id,
                "trace_id": rec.trace_id,
                "selector_step": rec.selector_step,
                "min_extra": min_extra,
                "min_k": min_k,
                "baseline_routing_quality_score": baseline_score,
                "replay_selected_tools": selected_tools,
                "replay_routing_quality_score": score,
                "score_delta_vs_baseline": score - baseline_score,
            }

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map = {executor.submit(_run_one, job): job for job in jobs}
            for future in as_completed(future_map):
                row = future.result()
                all_scores.append(row)
                model_extra_scores[(row["model"], row["min_extra"])].append(row["replay_routing_quality_score"])
                overall_extra_scores[row["min_extra"]].append(row["replay_routing_quality_score"])
                with progress_lock:
                    current_job += 1
                    print(
                        f"  [{current_job}/{total_jobs}] model={row['model']} record={row['record_id']} "
                        f"min_extra={row['min_extra']} min_k={row['min_k']} "
                        f"score={row['replay_routing_quality_score']:.4f}"
                    )

        per_model_summary: Dict[str, Any] = {}
        for model in sorted({r.model for r in sampled_records}):
            curve = []
            for min_extra in range(args.min_extra_start, args.min_extra_end + 1):
                score_list = model_extra_scores.get((model, min_extra), [])
                curve.append(
                    {
                        "min_extra": min_extra,
                        "mean_routing_quality_score": _mean(score_list),
                        "samples": len(score_list),
                    }
                )
            best = max(curve, key=lambda x: (x["mean_routing_quality_score"], -x["min_extra"]))
            per_model_summary[model] = {
                "curve": curve,
                "best_min_extra": best["min_extra"],
                "best_mean_routing_quality_score": best["mean_routing_quality_score"],
            }

        overall_curve = []
        for min_extra in range(args.min_extra_start, args.min_extra_end + 1):
            score_list = overall_extra_scores.get(min_extra, [])
            overall_curve.append(
                {
                    "min_extra": min_extra,
                    "mean_routing_quality_score": _mean(score_list),
                    "samples": len(score_list),
                }
            )
        overall_best = max(
            overall_curve,
            key=lambda x: (x["mean_routing_quality_score"], -x["min_extra"]),
        )

        analysis_payload = {
            "generated_at_utc": run_id,
            "db_path": str(db_path),
            "sample_size_per_model": args.sample_size,
            "seed": args.seed,
            "scan_range": [args.min_extra_start, args.min_extra_end],
            "records_count": len(sampled_records),
            "overall": {
                "curve": overall_curve,
                "best_min_extra": overall_best["min_extra"],
                "best_mean_routing_quality_score": overall_best["mean_routing_quality_score"],
            },
            "per_model": per_model_summary,
            "record_level_scores": all_scores,
        }

        result_json_path = output_dir / f"tool_selector_min_extra_analysis_{run_id}.json"
        result_json_path.write_text(
            json.dumps(analysis_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result_csv_path = output_dir / f"tool_selector_min_extra_curve_{run_id}.csv"
        with result_csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "scope",
                    "model",
                    "min_extra",
                    "mean_routing_quality_score",
                    "samples",
                ],
            )
            writer.writeheader()
            for row in overall_curve:
                writer.writerow(
                    {
                        "scope": "overall",
                        "model": "ALL",
                        "min_extra": row["min_extra"],
                        "mean_routing_quality_score": row["mean_routing_quality_score"],
                        "samples": row["samples"],
                    }
                )
            for model, payload in per_model_summary.items():
                for row in payload["curve"]:
                    writer.writerow(
                        {
                            "scope": "per_model",
                            "model": model,
                            "min_extra": row["min_extra"],
                            "mean_routing_quality_score": row["mean_routing_quality_score"],
                            "samples": row["samples"],
                        }
                    )

        print("[4/4] 分析完成。")
        print(f"- 详细结果(JSON): {result_json_path}")
        print(f"- 曲线摘要(CSV): {result_csv_path}")
        print(
            f"- Overall best min_extra={overall_best['min_extra']}, "
            f"mean_score={overall_best['mean_routing_quality_score']:.6f}"
        )
        for model, payload in sorted(per_model_summary.items()):
            print(
                f"  * {model}: best_min_extra={payload['best_min_extra']} "
                f"score={payload['best_mean_routing_quality_score']:.6f}"
            )
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
